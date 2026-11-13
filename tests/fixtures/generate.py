"""Build small synthetic media fixtures with real ffmpeg (lavfi sources).

Nothing binary is committed to the repository (STEP 22/23 of the task
spec: real-media E2E, not fake CLI stand-ins). Every fixture here is
generated fresh, at test-session start, from a documented ffmpeg
invocation so the expected measurement values are known exactly.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from typing import Dict


def _run(*args: str) -> None:
    result = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"fixture generation failed: {' '.join(args)}\n{result.stderr}")


def _sine_samples(*, sample_rate: int, duration_sec: int, frequency: int, amplitude: float) -> bytes:
    """16-bit PCM samples for a sine wave at an explicit amplitude
    (fraction of full scale), computed directly rather than through any
    ffmpeg source filter.
    """

    peak = amplitude * 32767
    frames = bytearray()
    for i in range(sample_rate * duration_sec):
        value = int(round(peak * math.sin(2 * math.pi * frequency * (i / sample_rate))))
        frames += struct.pack("<h", value)
    return bytes(frames)


def _write_clipped_square_wave(path: Path, *, sample_rate: int, duration_sec: int, frequency: int) -> None:
    """Write mono 16-bit PCM samples pinned to full scale (+/-32767) - a
    square wave, not a sine, so every single sample (not just the crest of
    a sine) sits at the digital ceiling. This is already clipped audio by
    construction; no encoder or filter is involved in producing it.
    """

    total_samples = sample_rate * duration_sec
    samples_per_half_cycle = max(1, sample_rate // (frequency * 2))
    frames = bytearray()
    high = True
    counter = 0
    for _ in range(total_samples):
        frames += struct.pack("<h", 32767 if high else -32767)
        counter += 1
        if counter >= samples_per_half_cycle:
            counter = 0
            high = not high

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(frames))


def build_all(out_dir: Path) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}

    # clean.mp4: 4s, 320x240 @25fps, h264, mono sine tone audio at a
    # moderate level - no black frames, no freeze, no clipping.
    clean = out_dir / "clean.mp4"
    _run(
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4,volume=-6dB",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clean),
    )
    paths["clean"] = clean

    # video_no_audio.mp4: same picture, no audio stream at all.
    no_audio = out_dir / "video_no_audio.mp4"
    _run(
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(no_audio),
    )
    paths["video_no_audio"] = no_audio

    # black.mp4: 2s of picture then 1.5s of solid black -> one black segment.
    black_a = out_dir / "_black_a.mp4"
    black_b = out_dir / "_black_b.mp4"
    black = out_dir / "black.mp4"
    _run("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2", "-pix_fmt", "yuv420p", str(black_a))
    _run("-f", "lavfi", "-i", "color=black:size=320x240:rate=25:duration=1.5", "-pix_fmt", "yuv420p", str(black_b))
    _run(
        "-i", str(black_a), "-i", str(black_b),
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[out]", "-map", "[out]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(black),
    )
    paths["black"] = black

    # freeze.mp4: 2s of motion, then the last frame held for 2s (tpad clone).
    freeze = out_dir / "freeze.mp4"
    _run(
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2",
        "-vf", "tpad=stop_mode=clone:stop_duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(freeze),
    )
    paths["freeze"] = freeze

    # illegal_luminance.mp4: 1s of legal-range gray, then 1s forced to Y=250
    # (superwhite, illegal for 8-bit limited-range video) via lutyuv - a
    # deliberate, exact pixel value, not a guess (see ADR-013: verified
    # against `signalstats` directly before this fixture was written).
    legal_part = out_dir / "_legal.mp4"
    illegal_part = out_dir / "_illegal.mp4"
    illegal_luminance = out_dir / "illegal_luminance.mp4"
    _run("-f", "lavfi", "-i", "color=gray:size=320x240:rate=25:duration=1", "-pix_fmt", "yuv420p", str(legal_part))
    _run(
        "-f", "lavfi", "-i", "color=gray:size=320x240:rate=25:duration=1",
        "-vf", "lutyuv=y=250", "-pix_fmt", "yuv420p", str(illegal_part),
    )
    _run(
        "-i", str(legal_part), "-i", str(illegal_part),
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[out]", "-map", "[out]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(illegal_luminance),
    )
    paths["illegal_luminance"] = illegal_luminance
    for name in ("_legal.mp4", "_illegal.mp4"):
        (out_dir / name).unlink(missing_ok=True)

    # corrupted.mp4: clean.mp4 with a run of zero bytes stamped into the
    # middle of the file, guaranteed to land inside compressed frame data.
    corrupted = out_dir / "corrupted.mp4"
    shutil.copyfile(clean, corrupted)
    size = corrupted.stat().st_size
    with open(corrupted, "r+b") as fh:
        fh.seek(size // 3)
        fh.write(b"\x00" * 4000)
    paths["corrupted"] = corrupted

    # silence_gap.wav: 2s tone, 2s silence, 2s tone -> one internal silence.
    # Written directly as 16-bit PCM with Python's stdlib `wave` and `math`
    # modules (no ffmpeg lavfi source involved), at an explicit amplitude
    # (30% of full scale, about -10.5 dBFS) safely above the -30dB
    # silencedetect threshold used below - avoiding any dependency on an
    # ffmpeg source filter's own default gain (see loud_clipping.wav above
    # for why that matters: it was observed to differ across ffmpeg
    # builds/platforms).
    silence_gap = out_dir / "silence_gap.wav"
    sample_rate = 44100
    tone_samples = _sine_samples(sample_rate=sample_rate, duration_sec=2, frequency=880, amplitude=0.3)
    silence_samples = struct.pack("<h", 0) * (sample_rate * 2)
    with wave.open(str(silence_gap), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(tone_samples + silence_samples + tone_samples)
    paths["silence_gap"] = silence_gap

    # loud_clipping.wav: written directly as 16-bit PCM at full scale
    # (+/-32767) with Python's stdlib `wave` module - no ffmpeg generation
    # step at all, so there is no dependency on any lavfi source's default
    # gain or on how a given ffmpeg build's filter chain handles
    # out-of-range samples (both were observed, empirically, to differ
    # across ffmpeg builds/platforms - see silence_gap.wav above). The
    # file already contains guaranteed, unambiguous digital clipping;
    # ffmpeg is only used to *measure* it via astats/ebur128.
    loud_clipping = out_dir / "loud_clipping.wav"
    _write_clipped_square_wave(loud_clipping, sample_rate=44100, duration_sec=2, frequency=200)
    paths["loud_clipping"] = loud_clipping

    # subtitle_valid.srt: cues aligned with clean.mp4's 4s duration.
    subtitle_valid = out_dir / "subtitle_valid.srt"
    subtitle_valid.write_text(
        "1\n00:00:00,200 --> 00:00:01,800\nHello there\n\n"
        "2\n00:00:02,000 --> 00:00:03,800\nGeneral Kenobi\n\n",
        encoding="utf-8",
    )
    paths["subtitle_valid"] = subtitle_valid

    # subtitle_mismatch.srt: ends far short of clean.mp4's 4s duration.
    subtitle_mismatch = out_dir / "subtitle_mismatch.srt"
    subtitle_mismatch.write_text("1\n00:00:00,000 --> 00:00:01,000\nOnly the start\n\n", encoding="utf-8")
    paths["subtitle_mismatch"] = subtitle_mismatch

    # subtitle_malformed.srt: invalid timestamp order, duplicate id, empty cue.
    subtitle_malformed = out_dir / "subtitle_malformed.srt"
    subtitle_malformed.write_text(
        "1\n00:00:01,000 --> 00:00:00,500\nBackwards timing\n\n"
        "1\n00:00:02,000 --> 00:00:03,000\nDuplicate id of cue 1\n\n"
        "3\n00:00:04,000 --> 00:00:05,000\n\n",
        encoding="utf-8",
    )
    paths["subtitle_malformed"] = subtitle_malformed

    for name in ("_black_a.mp4", "_black_b.mp4"):
        (out_dir / name).unlink(missing_ok=True)

    _build_visual_qc_fixtures(out_dir, paths)
    _build_reference_fixtures(out_dir, paths)
    _build_stinger_fixtures(out_dir, paths)
    _build_loop_seam_fixtures(out_dir, paths)
    _build_video_material_fixtures(out_dir, paths)
    return paths


def _geq_clip(path: Path, *, size: str, rate: int, duration: int, lum: str) -> None:
    """A gray-only clip whose luma is an exact per-pixel/per-frame
    expression (``geq``; N = frame index, X/Y = pixel position)."""

    _run(
        "-f", "lavfi", "-i", f"color=c=black:s={size}:r={rate}:d={duration},geq=lum='{lum}':cb=128:cr=128",
        "-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv420p", str(path),
    )


def _build_visual_qc_fixtures(out_dir: Path, paths: Dict[str, Path]) -> None:
    """ADR-016/017/018 fixtures. Y=235 / Y=16 are limited-range white /
    black (200 / 0 cd/m2 under BT.1702's SDR mapping)."""

    # Photosensitive flash (20 fps, 3 s, 160x90). Alternating every frame
    # = 10 Hz (20 luminance changes, 10 flashes per second).
    flash_cases = {
        "flash_10hz": "if(lt(mod(N,2),1),235,16)",                       # full screen, 10 Hz -> FAIL
        "flash_2hz": "if(lt(mod(N,10),5),235,16)",                       # full screen, 2 Hz -> PASS
        "flash_small_area": "if(lt(X,48)*lt(Y,27)*lt(mod(N,2),1),235,16)",  # 9% of the screen, 10 Hz -> PASS
        "flash_half_area": "if(lt(X,80)*lt(mod(N,2),1),235,16)",          # 50% of the screen, 10 Hz -> FAIL
        "flash_dim": "if(lt(mod(N,2),1),40,16)",                          # ~1 cd/m2 swing (< 20) -> PASS
        "flash_bright_only": "if(lt(mod(N,2),1),235,225)",                # darker side ~179 cd/m2 (>= 160) -> PASS
    }
    for name, lum in flash_cases.items():
        path = out_dir / f"{name}.mp4"
        _geq_clip(path, size="160x90", rate=20, duration=3, lum=lum)
        paths[name] = path

    # Planned cuts: 25 fps, 4 s, four flat shots with cuts at frames 25,
    # 60, 90 (= 1.0 s, 2.4 s, 3.6 s).
    cuts = out_dir / "cuts.mp4"
    _geq_clip(cuts, size="160x90", rate=25, duration=4, lum="if(lt(N,25),40,if(lt(N,60),200,if(lt(N,90),90,160)))")
    paths["cuts"] = cuts

    # Text legibility: 640x360 (short side 360 -> x3 to 1080), 2 s, on a
    # #202020 background. "Glyphs" are drawbox bars so their height is
    # exact on every platform (no font dependency):
    #   A box [40,40,200,80]:   white bars, 40 px tall   -> high contrast, 120 px @1080
    #   B box [320,40,200,80]:  #2A2A2A bars, 40 px tall -> contrast ~1.1:1
    #   C box [40,220,200,40]:  white bars, 6 px tall    -> 18 px @1080
    bars = []
    for i in range(4):
        bars.append(f"drawbox=x={60 + 30 * i}:y=60:w=8:h=40:color=white:t=fill")
        bars.append(f"drawbox=x={340 + 30 * i}:y=60:w=8:h=40:color=0x2A2A2A:t=fill")
        bars.append(f"drawbox=x={60 + 30 * i}:y=236:w=16:h=6:color=white:t=fill")
    text_boxes = out_dir / "text_boxes.mp4"
    _run(
        "-f", "lavfi", "-i", "color=c=0x202020:s=640x360:r=25:d=2",
        "-vf", ",".join(bars), "-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv420p", str(text_boxes),
    )
    paths["text_boxes"] = text_boxes


def _build_reference_fixtures(out_dir: Path, paths: Dict[str, Path]) -> None:
    """ADR-021 fixtures: a near-lossless 320x240 @25fps, 2 s master and
    distorted versions of it."""

    master = out_dir / "ref_master.mp4"
    _run(
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2",
        "-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv420p", str(master),
    )
    paths["ref_master"] = master

    # Same frames, crushed by the encoder (crf 51) and blurred: SSIM ~0.8,
    # PSNR ~25 dB - far below the 0.95 / 31 dB / VMAF 93 defaults.
    degraded = out_dir / "ref_degraded.mp4"
    _run("-i", str(master), "-vf", "boxblur=2:1", "-c:v", "libx264", "-crf", "51", "-pix_fmt", "yuv420p", str(degraded))
    paths["ref_degraded"] = degraded

    # Same frames at half size: compared after a bicubic upscale to 320x240.
    small = out_dir / "ref_small.mp4"
    _run("-i", str(master), "-vf", "scale=160:120", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(small))
    paths["ref_small"] = small

    # 30 fps instead of 25: refused (FRAME_RATE_MISMATCH).
    fps30 = out_dir / "ref_30fps.mp4"
    _run(
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=2",
        "-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv420p", str(fps30),
    )
    paths["ref_30fps"] = fps30

    # First second only (25 of 50 frames): refused (FRAME_COUNT_MISMATCH).
    short = out_dir / "ref_short.mp4"
    _run("-i", str(master), "-frames:v", "25", "-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv420p", str(short))
    paths["ref_short"] = short


# ADR-024: a 30 fps, 64x36 stinger whose graphic (light grey, 230) covers the
# left half for frames 0-12, three quarters for frames 13-14, the whole
# picture for frames 15-17 (0.5 s to 0.6 s: cut point 0.5 s, cover [0.5, 0.6))
# and the right half from frame 18 to its end (1.2 s, 36 frames).
STINGER_ALPHA = (
    "if(lte(N,12), if(lt(X,W/2),255,0), if(lte(N,14), if(lt(X,3*W/4),255,0), "
    "if(lte(N,17), 255, if(gte(X,W/2),255,0))))"
)


def _build_stinger_fixtures(out_dir: Path, paths: Dict[str, Path]) -> None:
    """ADR-024 fixtures: stingers with alpha and edits made with them."""

    wipe = out_dir / "stinger_wipe.mov"  # QuickTime Animation, 8-bit argb
    _run(
        "-f", "lavfi", "-i",
        f"color=c=black:s=64x36:r=30:d=1.2,format=rgba,geq=r='230':g='230':b='230':a='{STINGER_ALPHA}'",
        "-c:v", "qtrle", str(wipe),
    )
    paths["stinger_wipe"] = wipe

    prores = out_dir / "stinger_prores.mov"  # ProRes 4444: alpha decoded at more than 8 bits
    _run("-i", str(wipe), "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(prores))
    paths["stinger_prores"] = prores

    # Frame 10: every alpha exactly 253 (opaque by the rule); frame 20: one
    # pixel at 252 (not); every other frame transparent.
    edge = out_dir / "stinger_edge.mov"
    _run(
        "-f", "lavfi", "-i",
        "color=c=black:s=64x36:r=30:d=1,format=rgba,geq=r='230':g='230':b='230':"
        "a='if(eq(N,10),253,if(eq(N,20),if(eq(X,0)*eq(Y,0),252,255),0))'",
        "-c:v", "qtrle", str(edge),
    )
    paths["stinger_edge"] = edge

    # Red 2 s then blue 2 s, joined at frame 60 (2.0 s), the stinger laid
    # over the join so its frame 15 lands on frame 60 (starting at 1.5 s);
    # "late" starts it one frame later, so frame 60 shows its frame 14.
    for name, start_frames in (("stinger_edit", 45), ("stinger_edit_late", 46)):
        path = out_dir / f"{name}.mp4"
        _run(
            "-f", "lavfi", "-i", "color=c=red:s=64x36:r=30:d=2",
            "-f", "lavfi", "-i", "color=c=blue:s=64x36:r=30:d=2",
            "-i", str(wipe),
            "-filter_complex",
            f"[0:v][1:v]concat=n=2:v=1[base];[2:v]setpts=PTS+{start_frames}/(30*TB)[st];"
            "[base][st]overlay=eof_action=pass[out]",
            "-map", "[out]", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(path),
        )
        paths[name] = path


def _aeval_wav(path: Path, expr: str, duration: float, rate: int = 16000) -> None:
    _run("-f", "lavfi", "-i", f"aevalsrc='{expr}':s={rate}:d={duration}", "-c:a", "pcm_s16le", str(path))


def _repeat_wav(src: Path, dst: Path, times: int) -> None:
    """``src`` repeated end to end, sample for sample."""

    with wave.open(str(src), "rb") as r:
        params, frames = r.getparams(), r.readframes(r.getnframes())
    with wave.open(str(dst), "wb") as w:
        w.setparams(params)
        w.writeframes(frames * times)


def _build_loop_seam_fixtures(out_dir: Path, paths: Dict[str, Path]) -> None:
    """ADR-025 fixtures, 16 kHz 16-bit PCM. The tone is 250 Hz + 1000 Hz,
    whole cycles in 2 s, so a 2 s loop wraps continuously and a 2.003 s one
    jumps; the 4 s beds change level or spectral balance over one period
    of a slow sine, continuous at the wrap but different on its two sides."""

    tone = "0.4*sin(2*PI*250*t)+0.2*sin(2*PI*1000*t)"
    seamless = out_dir / "loop_seamless.wav"
    _aeval_wav(seamless, tone, 2)
    paths["loop_seamless"] = seamless
    naive = out_dir / "loop_naive.wav"
    _aeval_wav(naive, tone, 2.003)
    paths["loop_naive"] = naive
    for name, src in (("loop_repeat10", seamless), ("loop_naive_repeat10", naive)):
        path = out_dir / f"{name}.wav"
        _repeat_wav(src, path, 10)
        paths[name] = path
    level = out_dir / "loop_level_step.wav"  # quieter before the wrap than after it
    _aeval_wav(level, "(0.3+0.2*sin(2*PI*t/4))*sin(2*PI*250*t)", 4)
    paths["loop_level_step"] = level
    spectrum = out_dir / "loop_spectrum_step.wav"  # 2000 Hz before the wrap, 250 Hz after it, at one level
    _aeval_wav(
        spectrum,
        "(0.5+0.5*sin(2*PI*t/4))*0.4*sin(2*PI*250*t)+(0.5-0.5*sin(2*PI*t/4))*0.4*sin(2*PI*2000*t)", 4,
    )
    paths["loop_spectrum_step"] = spectrum
    silence = out_dir / "loop_silence.wav"
    _aeval_wav(silence, "0", 2)
    paths["loop_silence"] = silence
    stereo = out_dir / "loop_stereo_click.wav"  # the right channel flips phase at 1.0 s: a jump there and at the wrap
    _aeval_wav(stereo, "0.4*sin(2*PI*250*t)|0.4*sin(2*PI*250*t+PI*gt(t,1))", 2)
    paths["loop_stereo_click"] = stereo

    # 1 s of digital silence whose very last sample is half scale: a click out of silence at the wrap.
    from_silence = out_dir / "loop_click_from_silence.wav"
    with wave.open(str(from_silence), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 15999 + struct.pack("<h", 16384))
    paths["loop_click_from_silence"] = from_silence
    empty = out_dir / "loop_empty.wav"  # a WAV header and no samples
    with wave.open(str(empty), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"")
    paths["loop_empty"] = empty


# ADR-026: 64x36 @30fps loops. ORBIT is a soft dot going once round an
# ellipse every 30 frames (a seamless loop like a rendered title loop);
# PAN is a texture periodic in 32 px panned 1 px a frame, so 32 frames are
# one whole period. "_short" drops the last frame: the seam then steps two
# frames' worth.
ORBIT = (
    "255*exp(-((X-32-12*cos(2*PI*{n}/30))*(X-32-12*cos(2*PI*{n}/30))"
    "+(Y-18-8*sin(2*PI*{n}/30))*(Y-18-8*sin(2*PI*{n}/30)))/18)"
)
PAN = "128+90*sin(2*PI*(X+N)/32)*cos(2*PI*Y/18)"
# ADR-028: a 230/200/60 box, 24x16 px, moving right 1 px a frame over black.
KEY_BOX = "between(X\\,4+N\\,27+N)*between(Y\\,10\\,25)"
KEY_LIT = "eq(N\\,10)*eq(X\\,60)*eq(Y\\,2)"  # one white pixel under alpha 0 on frame 10
PRORES_HQ = ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0", "-pix_fmt", "yuv422p10le", "-an"]
PRORES_4444 = ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le"]


def _gray_loop(path: Path, lum: str, frames: int) -> None:
    _run(
        "-f", "lavfi", "-i", f"color=c=black:s=64x36:r=30:d=4,format=gray,geq=lum='{lum}'",
        "-frames:v", str(frames), "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(path),
    )


def _key_fill_pair(src: Path, fill: Path, key: Path) -> None:
    """ffmpeg-skill's ``export --preset key-fill`` chain (straight fill),
    one decode split into a ProRes 422 HQ fill and key."""

    _run(
        "-i", str(src), "-filter_complex",
        "[0:v]split[f][k];[f]format=yuv422p10le[fill];[k]alphaextract,format=yuv422p10le[key]",
        "-map", "[fill]", *PRORES_HQ, str(fill), "-map", "[key]", *PRORES_HQ, str(key),
    )


def _build_video_material_fixtures(out_dir: Path, paths: Dict[str, Path]) -> None:
    """ADR-026/027/028 fixtures: loops, alpha edges, key / fill pairs."""

    for name, lum, frames in (
        ("vloop_orbit", ORBIT.format(n="N"), 30), ("vloop_orbit_short", ORBIT.format(n="N"), 29),
        ("vloop_pan", PAN, 32), ("vloop_pan_short", PAN, 31), ("vloop_one_frame", PAN, 1),
    ):
        path = out_dir / f"{name}.mp4"
        _gray_loop(path, lum, frames)
        paths[name] = path
    # A title clip: IN fades the dot up over 10 frames, LOOP is one orbit
    # (30 frames), OUT fades it down over 10: segments {in: 1/3, loop: 1, out: 1/3}.
    title = out_dir / "vloop_title.mp4"
    fade = "min(1\\,min((N+1)/10\\,(50-N)/10))"
    _gray_loop(title, f"{fade}*" + ORBIT.format(n="(N-10)"), 50)
    paths["vloop_title"] = title
    # The orbit with its picture data zeroed: it probes (moov first), no frame decodes.
    undecodable = out_dir / "vloop_undecodable.mp4"
    _run("-i", str(out_dir / "vloop_orbit.mp4"), "-c", "copy", "-movflags", "+faststart", str(undecodable))
    data = bytearray(undecodable.read_bytes())
    at = data.find(b"mdat")
    data[at + 4:] = bytes(len(data) - at - 4)
    undecodable.write_bytes(bytes(data))
    paths["vloop_undecodable"] = undecodable

    # ADR-027: light grey (230) everywhere, alpha ramping 0 -> 255 over
    # x = 16..22 (five partly transparent columns, 42..213), straight; the
    # premultiplied copy is ffmpeg's premultiply of it.
    straight = out_dir / "alpha_straight.mov"
    _run(
        "-f", "lavfi", "-i",
        "color=c=black:s=64x36:r=30:d=1,format=rgba,geq=r='230':g='230':b='230':a='clip((X-16)*255/6\\,0\\,255)'",
        "-c:v", "qtrle", str(straight),
    )
    paths["alpha_straight"] = straight
    premult = out_dir / "alpha_premultiplied.mov"
    _run("-i", str(straight), "-vf", "premultiply=inplace=1", "-c:v", "qtrle", str(premult))
    paths["alpha_premultiplied"] = premult
    prores = out_dir / "alpha_straight_prores.mov"
    _run("-i", str(straight), *PRORES_4444, str(prores))
    paths["alpha_straight_prores"] = prores
    glow = out_dir / "alpha_glow.mov"  # partly transparent everywhere, nothing opaque: no edge to judge
    _run(
        "-f", "lavfi", "-i", "color=c=black:s=64x36:r=30:d=0.2,format=rgba,geq=r='230':g='230':b='230':a='128'",
        "-c:v", "qtrle", str(glow),
    )
    paths["alpha_glow"] = glow

    # ADR-028
    box = out_dir / "kf_source.mov"
    lit = out_dir / "kf_source_lit.mov"
    for path, extra in ((box, ""), (lit, f"+255*{KEY_LIT}")):
        _run(
            "-f", "lavfi", "-i",
            f"color=c=black:s=64x36:r=30:d=1,format=rgba,geq=r='230*{KEY_BOX}{extra}':g='200*{KEY_BOX}{extra}':"
            f"b='60*{KEY_BOX}{extra}':a='255*{KEY_BOX}'",
            *PRORES_4444, str(path),
        )
    paths["kf_source"] = box
    for name, src in (("kf", box), ("kf_lit", lit)):
        fill, key = out_dir / f"{name}_fill.mov", out_dir / f"{name}_key.mov"
        _key_fill_pair(src, fill, key)
        paths[f"{name}_fill"], paths[f"{name}_key"] = fill, key
    late = out_dir / "kf_key_late.mov"  # the key one frame behind: frame n shows the source's alpha at n - 1
    _run("-i", str(box), "-vf", "alphaextract,tpad=start=1:start_mode=clone,trim=end_frame=30,format=yuv422p10le", *PRORES_HQ, str(late))
    paths["kf_key_late"] = late
    small = out_dir / "kf_key_small.mov"
    _run("-i", str(paths["kf_key"]), "-vf", "scale=64:34", *PRORES_HQ, str(small))
    paths["kf_key_small"] = small
    short = out_dir / "kf_key_short.mov"
    _run("-i", str(paths["kf_key"]), "-frames:v", "29", *PRORES_HQ, str(short))
    paths["kf_key_short"] = short
    tc = out_dir / "kf_key_tc.mov"
    _run("-i", str(paths["kf_key"]), "-timecode", "10:00:00:00", *PRORES_HQ, str(tc))
    paths["kf_key_tc"] = tc
    # A soft alpha ramp (0, 4, ..., 252 across x) through the same chain: the key
    # holds the alpha at full range in a stream that does not say so.
    soft = out_dir / "kf_soft_source.mov"
    _run(
        "-f", "lavfi", "-i", "color=c=black:s=64x36:r=30:d=0.2,format=rgba,geq=r='230':g='230':b='230':a='X*4'",
        *PRORES_4444, str(soft),
    )
    paths["kf_soft_source"] = soft
    _key_fill_pair(soft, out_dir / "kf_soft_fill.mov", out_dir / "kf_soft_key.mov")
    paths["kf_soft_key"] = out_dir / "kf_soft_key.mov"
