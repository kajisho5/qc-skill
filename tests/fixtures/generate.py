"""Build small synthetic media fixtures with real ffmpeg (lavfi sources).

Nothing binary is committed to the repository (STEP 22/23 of the task
spec: real-media E2E, not fake CLI stand-ins). Every fixture here is
generated fresh, at test-session start, from a documented ffmpeg
invocation so the expected measurement values are known exactly.
"""

from __future__ import annotations

import shutil
import subprocess
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
    # The tone is generated with aevalsrc at an explicit amplitude (0.3,
    # about -10.5 dBFS) rather than relying on the `sine` source's default
    # gain, which is an undocumented implementation detail that has been
    # observed to differ across ffmpeg builds/platforms - an explicit
    # amplitude keeps this fixture's level (and therefore its relationship
    # to the -30dB silencedetect threshold below) deterministic everywhere.
    tone_a = out_dir / "_tone_a.wav"
    silence_b = out_dir / "_silence_b.wav"
    tone_c = out_dir / "_tone_c.wav"
    silence_gap = out_dir / "silence_gap.wav"
    _run("-f", "lavfi", "-i", "aevalsrc=0.3*sin(880*2*PI*t):s=44100:d=2", "-c:a", "pcm_s16le", str(tone_a))
    _run("-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:duration=2", "-c:a", "pcm_s16le", str(silence_b))
    _run("-f", "lavfi", "-i", "aevalsrc=0.3*sin(880*2*PI*t):s=44100:d=2", "-c:a", "pcm_s16le", str(tone_c))
    _run(
        "-i", str(tone_a), "-i", str(silence_b), "-i", str(tone_c),
        "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]", "-map", "[out]",
        "-c:a", "pcm_s16le", str(silence_gap),
    )
    paths["silence_gap"] = silence_gap

    # loud_clipping.wav: an explicit-amplitude sine (2.5, well past the
    # [-1.0, 1.0] float range) fed straight into a 16-bit PCM encoder,
    # which hard-clamps every sample to full scale -> guaranteed digital
    # clipping. Built with aevalsrc rather than `sine=...,volume=NdB` so
    # the result does not depend on the `sine` source's own default gain,
    # which is an undocumented implementation detail observed to differ
    # across ffmpeg builds/platforms (see silence_gap.wav above).
    loud_clipping = out_dir / "loud_clipping.wav"
    _run(
        "-f", "lavfi", "-i", "aevalsrc=2.5*sin(1000*2*PI*t):s=44100:d=2",
        "-c:a", "pcm_s16le", str(loud_clipping),
    )
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

    for name in ("_black_a.mp4", "_black_b.mp4", "_tone_a.wav", "_silence_b.wav", "_tone_c.wav"):
        (out_dir / name).unlink(missing_ok=True)

    return paths
