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

    return paths
