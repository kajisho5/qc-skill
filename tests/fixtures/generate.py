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
