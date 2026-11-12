"""Loop-seam measurements for audio (ADR-025) - measured facts only.

An audio file declared loopable is played end -> start; a file made of
repeats has seams inside it too. For each seam three facts are measured
from the decoded samples (ffmpeg decodes to 32-bit float at the file's own
rate and channel count; everything else is pure Python):

  * **Click.** The second difference ``x[i-1] - 2 x[i] + x[i+1]`` turns a
    step or a kink at the seam into a spike while staying small on smooth
    material. ``click_ratio`` = the largest ``|second difference|``
    within ``CLICK_WINDOW_SEC`` of the seam / the 99th percentile
    (nearest rank) of ``|second difference|`` in the surrounding
    ``CLICK_CONTEXT_SEC`` on both sides, outside that window. A continuous
    seam measures about 1; a jump measures far more. When the context is
    digital silence the ratio is 0 if the seam is silent too, else it is
    reported as ``null`` with ``from_silence: true`` (a click out of
    silence). Per channel; the worst channel is reported.
  * **Level.** RMS of the last ``window_sec`` before the seam and of the
    first ``window_sec`` after it, in dBFS (``20 log10 rms``, full scale
    1.0, floored at ``LEVEL_FLOOR_DB``); ``diff_db`` is their absolute
    difference. Per channel; the worst channel is reported.
  * **Spectrum.** Octave-band levels of the same two windows, per
    channel: Hann-windowed, non-overlapping frames of a power-of-two
    length (about a tenth of a second), averaged; bands centred on
    62.5 Hz * 2^k up to the Nyquist frequency, each spanning a factor of
    two. Each band is taken relative to its side's total, so the shape is
    compared and a level change alone is left to the level fact. Bands
    more than ``BAND_RANGE_DB`` below the loudest (over both windows) are
    not compared - they carry no audible share of the spectrum.
    ``max_diff_db`` is the largest absolute band difference; the worst
    channel is reported. A side that is digital silence has no shape:
    0 when both sides are, ``null`` with ``silent_side`` when one is.

For the wrap the "before" side is the end of the file and the "after"
side its start, joined in memory. A seam whose windows do not fit in the
file is not measured for that fact (reason given), never padded.
"""

from __future__ import annotations

import cmath
import math
import sys
from array import array
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..errors import error
from ..models import QCMeasurement
from ..runner import ffmpeg_rawaudio_argv, run_argv

CLICK_WINDOW_SEC = 0.001  # the seam's own neighbourhood: +-1 ms
CLICK_CONTEXT_SEC = 0.050  # the reference: +-50 ms around it, outside the window
CLICK_CONTEXT_PERCENTILE = 99
LEVEL_FLOOR_DB = -120.0
BAND_RANGE_DB = 30.0
BAND_BASE_HZ = 62.5
MAX_DECODED_BYTES = 256 * 1024 * 1024  # 32-bit float samples held in memory at once

LOOP_SEAM_NOTE = (
    f"click_ratio = max |second difference| within +-{CLICK_WINDOW_SEC * 1000:g} ms of the seam / its "
    f"{CLICK_CONTEXT_PERCENTILE}th percentile within +-{CLICK_CONTEXT_SEC * 1000:g} ms outside that (worst channel); "
    f"level = RMS dBFS of window_sec on each side (worst channel, floor {LEVEL_FLOOR_DB:g}); "
    f"spectrum = octave bands from {BAND_BASE_HZ:g} Hz of the same windows relative to each side's total, bands within "
    f"{BAND_RANGE_DB:g} dB of the loudest compared (worst channel)"
)


# --- pure measurement functions (exposed for unit tests) ---------------------


def _percentile(values: Sequence[float], pct: int) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[rank - 1]


def click_spans(sample_rate: int) -> Tuple[int, int]:
    """``(window, context)`` in samples for the click measurement."""

    window = max(1, int(round(CLICK_WINDOW_SEC * sample_rate)))
    return window, max(window + 2, int(round(CLICK_CONTEXT_SEC * sample_rate)))


def click_ratio(signal: Sequence[float], junction: int, sample_rate: int) -> Dict[str, Any]:
    """The click measurement at ``junction`` (the index of the first
    sample after the seam) of one channel. ``signal`` must hold the
    click context plus one sample on both sides of the junction."""

    window, context = click_spans(sample_rate)
    peak = 0.0
    around: List[float] = []
    # d2 at i uses i-1, i, i+1; the ones touching both sides are i = junction - 1 and i = junction
    for i in range(junction - context, junction + context):
        d2 = abs(signal[i - 1] - 2.0 * signal[i] + signal[i + 1])
        if junction - window <= i < junction + window:
            peak = max(peak, d2)
        else:
            around.append(d2)
    reference = _percentile(around, CLICK_CONTEXT_PERCENTILE)
    step = abs(signal[junction] - signal[junction - 1])
    if reference > 0:
        ratio: Optional[float] = round(peak / reference, 4)
        from_silence = False
    else:
        ratio = 0.0 if peak == 0 else None
        from_silence = peak > 0
    return {
        "ratio": ratio, "from_silence": from_silence, "peak": round(peak, 9), "reference": round(reference, 9),
        "step": round(step, 9),
    }


def rms_dbfs(samples: Sequence[float]) -> float:
    if not samples:
        return LEVEL_FLOOR_DB
    mean_square = math.fsum(v * v for v in samples) / len(samples)
    if mean_square <= 0:
        return LEVEL_FLOOR_DB
    return round(max(LEVEL_FLOOR_DB, 10.0 * math.log10(mean_square)), 4)


_FFT_CACHE: Dict[int, Tuple[List[int], List[complex], List[float]]] = {}


def _fft_tables(n: int) -> Tuple[List[int], List[complex], List[float]]:
    if n not in _FFT_CACHE:
        bits = n.bit_length() - 1
        rev = [int(format(i, f"0{bits}b")[::-1], 2) for i in range(n)]
        twiddles = [cmath.exp(-2j * math.pi * k / n) for k in range(n // 2)]
        hann = [0.5 - 0.5 * math.cos(2.0 * math.pi * i / n) for i in range(n)]
        _FFT_CACHE[n] = (rev, twiddles, hann)
    return _FFT_CACHE[n]


def _fft(values: List[complex], rev: List[int], twiddles: List[complex]) -> List[complex]:
    n = len(values)
    out = [values[r] for r in rev]
    size = 2
    while size <= n:
        half = size // 2
        step = n // size
        for start in range(0, n, size):
            for k in range(half):
                t = twiddles[k * step] * out[start + k + half]
                u = out[start + k]
                out[start + k] = u + t
                out[start + k + half] = u - t
        size *= 2
    return out


def frame_length(sample_rate: int, window_samples: int) -> int:
    """The spectrum's frame: the largest power of two not above a tenth of
    a second, nor above the window."""

    limit = min(sample_rate // 10, window_samples)
    n = 1
    while n * 2 <= limit:
        n *= 2
    return n


def band_levels(samples: Sequence[float], sample_rate: int, frame: int) -> List[Tuple[float, float]]:
    """``[(centre_hz, level_db)]``: octave-band mean-square levels of one
    mono window, from Hann-windowed non-overlapping frames of ``frame``
    samples (a power of two). Levels are floored at ``LEVEL_FLOOR_DB``."""

    rev, twiddles, hann = _fft_tables(frame)
    frames = len(samples) // frame
    half = frame // 2
    power = [0.0] * (half + 1)
    for f in range(frames):
        base = f * frame
        spectrum = _fft([complex(samples[base + i] * hann[i]) for i in range(frame)], rev, twiddles)
        for k in range(1, half + 1):
            c = spectrum[k]
            power[k] += c.real * c.real + c.imag * c.imag
    norm = 2.0 / (frames * frame * sum(w * w for w in hann)) if frames else 0.0
    nyquist = sample_rate / 2.0
    bin_hz = sample_rate / frame
    out: List[Tuple[float, float]] = []
    centre = BAND_BASE_HZ
    while centre * math.sqrt(2.0) <= nyquist:
        low, high = centre / math.sqrt(2.0), centre * math.sqrt(2.0)
        bins = [k for k in range(1, half + 1) if low <= k * bin_hz < high]
        if bins:
            ms = sum(power[k] for k in bins) * norm
            level = 10.0 * math.log10(ms) if ms > 0 else LEVEL_FLOOR_DB
            out.append((centre, round(max(LEVEL_FLOOR_DB, level), 4)))
        centre *= 2.0
    return out


def _total_db(levels: List[Tuple[float, float]]) -> float:
    total = math.fsum(10.0 ** (lvl / 10.0) for _, lvl in levels if lvl > LEVEL_FLOOR_DB)
    return max(LEVEL_FLOOR_DB, 10.0 * math.log10(total)) if total > 0 else LEVEL_FLOOR_DB


def spectrum_difference(before: Sequence[float], after: Sequence[float], sample_rate: int) -> Dict[str, Any]:
    """The spectral shape of one channel on each side of a seam: each
    band's level relative to that side's total (so a level change alone
    does not count here - that is the level check's job), compared over
    the bands within ``BAND_RANGE_DB`` of the loudest. A side that is
    digital silence has no shape: ``max_diff_db`` is 0 when both are,
    ``None`` with ``silent_side`` when only one is."""

    frame = frame_length(sample_rate, min(len(before), len(after)))
    # whole frames only, the ones next to the seam: the end of "before", the start of "after"
    used = (min(len(before), len(after)) // frame) * frame
    a = band_levels(before[len(before) - used:], sample_rate, frame)
    b = band_levels(after[:used], sample_rate, frame)
    total_a, total_b = _total_db(a), _total_db(b)
    out: Dict[str, Any] = {"frame_samples": frame, "before_total_db": round(total_a, 4), "after_total_db": round(total_b, 4)}
    silent_a, silent_b = total_a <= LEVEL_FLOOR_DB, total_b <= LEVEL_FLOOR_DB
    if silent_a or silent_b:
        out.update(bands=[], bands_compared=0, max_diff_db=0.0 if (silent_a and silent_b) else None)
        if silent_a != silent_b:
            out["silent_side"] = "before" if silent_a else "after"
        return out
    rel_a = [(c, round(lvl - total_a, 4)) for c, lvl in a]
    rel_b = [(c, round(lvl - total_b, 4)) for c, lvl in b]
    loudest = max(lvl for _, lvl in rel_a + rel_b)
    bands = []
    worst = 0.0
    for (centre, la), (_, lb) in zip(rel_a, rel_b):
        compared = max(la, lb) >= loudest - BAND_RANGE_DB
        diff = round(abs(la - lb), 4)
        bands.append({"centre_hz": centre, "before_db": la, "after_db": lb, "diff_db": diff, "compared": compared})
        if compared:
            worst = max(worst, diff)
    out.update(bands=bands, bands_compared=sum(1 for x in bands if x["compared"]), max_diff_db=worst)
    return out


# --- decoding and per-seam assembly -----------------------------------------


def _channel(data: array, channels: int, ch: int, start: int, end: int) -> List[float]:
    return list(data[start * channels + ch:end * channels:channels])


def _measure_seam(
    data: array, channels: int, sample_rate: int, total: int, label: Any, junction: Optional[int], window: Optional[int],
) -> Dict[str, Any]:
    """One seam. ``junction=None`` is the wrap: the end of the file, then
    its start."""

    wrap = junction is None
    time_sec = round(total / sample_rate, 6) if wrap else label
    entry: Dict[str, Any] = {"seam": "wrap" if wrap else label, "time_sec": time_sec}
    if not wrap and junction > total:
        reason = f"the seam at {label}s is past the end of the audio ({round(total / sample_rate, 6)}s)"
        return {**entry, "click": {"measured": False, "reason": reason}, "level": {"measured": False, "reason": reason},
                "spectrum": {"measured": False, "reason": reason}}

    def sides(span: int) -> Optional[Tuple[List[List[float]], List[List[float]]]]:
        """``span`` samples before and after the seam, per channel."""
        if wrap:
            if 2 * span > total:
                return None
            return ([_channel(data, channels, c, total - span, total) for c in range(channels)],
                    [_channel(data, channels, c, 0, span) for c in range(channels)])
        if junction - span < 0 or junction + span > total:
            return None
        return ([_channel(data, channels, c, junction - span, junction) for c in range(channels)],
                [_channel(data, channels, c, junction, junction + span) for c in range(channels)])

    reach = click_spans(sample_rate)[1] + 1
    got = sides(reach)
    if got is None:
        entry["click"] = {"measured": False, "reason": f"the audio does not reach {CLICK_CONTEXT_SEC * 1000:g} ms on both sides of the seam"}
    else:
        worst = None
        for c in range(channels):
            one = click_ratio(got[0][c] + got[1][c], reach, sample_rate)
            one["channel"] = c
            key = math.inf if one["ratio"] is None else one["ratio"]
            if worst is None or key > worst[0]:
                worst = (key, one)
        entry["click"] = {"measured": True, **worst[1]}

    if window is None:
        reason = "no window_sec was given"
        entry["level"] = {"measured": False, "reason": reason}
        entry["spectrum"] = {"measured": False, "reason": reason}
        return entry
    got = sides(window)
    if got is None:
        where = "the file is shorter than two windows" if wrap else "the windows do not fit inside the file"
        reason = f"{where} ({window} samples on each side)"
        entry["level"] = {"measured": False, "reason": reason}
        entry["spectrum"] = {"measured": False, "reason": reason}
        return entry
    level = None
    for c in range(channels):
        before, after = rms_dbfs(got[0][c]), rms_dbfs(got[1][c])
        diff = round(abs(before - after), 4)
        if level is None or diff > level["diff_db"]:
            level = {"measured": True, "before_dbfs": before, "after_dbfs": after, "diff_db": diff, "channel": c}
    entry["level"] = level
    spectrum = None
    for c in range(channels):
        one = spectrum_difference(got[0][c], got[1][c], sample_rate)
        one["channel"] = c
        key = math.inf if one["max_diff_db"] is None else one["max_diff_db"]
        if spectrum is None or key > spectrum[0]:
            spectrum = (key, one)
    entry["spectrum"] = {"measured": True, **spectrum[1]}
    return entry


def _not_measured(reason: str) -> Dict[str, Any]:
    return {"measured": False, "reason": reason, "seams": []}


def analyze_loop_seams(
    ffmpeg_path: str,
    input_path: Path,
    audio_stream: Dict[str, Any],
    rule: Any,
    *,
    duration_sec: Optional[float],
    timeout: float = 300.0,
) -> List[QCMeasurement]:
    """``audio.loop_seams`` for a ``LoopSeamRule``: the wrap (unless
    ``include_wrap`` is false) and every listed seam time."""

    def result(value: Dict[str, Any]) -> List[QCMeasurement]:
        return [QCMeasurement("audio.loop_seams", "audio", "loop_seams", value, source="ffmpeg:pcm_f32le", notes=LOOP_SEAM_NOTE)]

    try:
        sample_rate = int(audio_stream.get("sample_rate"))
        channels = int(audio_stream.get("channels"))
    except (TypeError, ValueError):
        return result(_not_measured("the audio stream's sample rate or channel count could not be measured"))
    if sample_rate <= 0 or channels <= 0:
        return result(_not_measured("the audio stream's sample rate or channel count could not be measured"))
    if duration_sec is not None and duration_sec * sample_rate * channels * 4 > MAX_DECODED_BYTES:
        return result(_not_measured(f"the audio is too long to hold for the seam analysis (over {MAX_DECODED_BYTES // (1024 * 1024)} MiB of samples)"))

    argv = ffmpeg_rawaudio_argv(ffmpeg_path, input_path, stream_select=f"0:{audio_stream['index']}")
    try:
        res = run_argv(argv, timeout=timeout, binary_stdout=True)
    except FileNotFoundError:
        raise error("DEPENDENCY_ERROR", "ffmpeg executable could not be run", executable=ffmpeg_path)
    if res.timed_out:
        raise error("TOOL_ERROR", "ffmpeg loop-seam decode timed out", argv=argv, timeout=timeout)
    raw = res.stdout_bytes
    usable = len(raw) - len(raw) % (4 * channels)
    if res.returncode != 0 or usable == 0:
        detail = (res.stderr or "").strip().splitlines()[-1:] or ["no samples"]
        return result(_not_measured(f"the audio could not be decoded: {detail[0]}"))
    data = array("f")
    data.frombytes(raw[:usable])
    if sys.byteorder == "big":  # the samples are f32le
        data.byteswap()
    total = len(data) // channels

    window = None
    if rule.window_sec is not None:
        window = int(round(rule.window_sec * sample_rate))
    seams = []
    if rule.include_wrap:
        seams.append(_measure_seam(data, channels, sample_rate, total, "wrap", None, window))
    for t in rule.seams or []:
        seams.append(_measure_seam(data, channels, sample_rate, total, t, int(round(t * sample_rate)), window))
    return result({
        "measured": True, "sample_rate": sample_rate, "channels": channels, "samples": total,
        "duration_sec": round(total / sample_rate, 6), "window_sec": rule.window_sec,
        "window_samples": window, "seams": seams,
    })


def not_performed(reason: str) -> List[QCMeasurement]:
    return [QCMeasurement("audio.loop_seams", "audio", "loop_seams", _not_measured(reason), source="ffmpeg:pcm_f32le", notes=LOOP_SEAM_NOTE)]
