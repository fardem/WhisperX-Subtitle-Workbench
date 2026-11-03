"""Gap check: loud, voice-like passages without text – with synthesized audio."""

import numpy as np
import pytest

import wt_core as core

SR = core.SAMPLE_RATE


def base(seconds=10.0, seed=1):
    """Quiet background noise (about -60 dBFS)."""
    return (np.random.default_rng(seed).standard_normal(int(SR * seconds)) * 0.001).astype(np.float32)


def voice(seconds):
    """Voice-like signal: fundamental 150 Hz plus overtones."""
    t = np.arange(int(SR * seconds)) / SR
    return (sum(np.sin(2 * np.pi * f * t) / k for k, f in enumerate((150, 300, 450, 600, 750), 1)) * 0.05
            ).astype(np.float32)


def result(*spans):
    """WhisperX result with one word per (start, end)."""
    return {"segments": [{"start": s, "end": e, "words": [{"word": "x", "start": s, "end": e}]} for s, e in spans]}


def put(audio, start, signal):
    a = int(start * SR)
    audio[a:a + len(signal)] += signal
    return audio


def test_voice_without_text_is_found():
    audio = put(put(base(), 0.0, voice(2)), 5.0, voice(2))
    gaps = core.find_gaps(audio, result((0.0, 2.0)))
    assert len(gaps) == 1
    start, end, _ = gaps[0]
    assert start == pytest.approx(5.0, abs=0.1) and end == pytest.approx(7.0, abs=0.1)


def test_voice_with_text_is_no_gap():
    audio = put(put(base(), 0.0, voice(2)), 5.0, voice(2))
    assert core.find_gaps(audio, result((0.0, 2.0), (5.0, 7.0))) == []


def test_noise_is_ignored():
    # loud but noise-like (applause, rustling): spectrally flat → not voice-like
    audio = put(base(), 0.0, voice(2))
    audio = put(audio, 5.0, (np.random.default_rng(2).standard_normal(2 * SR) * 0.1).astype(np.float32))
    assert core.find_gaps(audio, result((0.0, 2.0))) == []


def test_short_passages_are_ignored():
    audio = put(put(base(), 0.0, voice(2)), 5.0, voice(0.5))
    assert core.find_gaps(audio, result((0.0, 2.0)), min_len=1.0) == []


def test_digital_silence_and_empty_audio():
    assert core.find_gaps(np.zeros(SR * 5, np.float32), result()) == []
    assert core.find_gaps(np.zeros(0, np.float32), result()) == []


def test_gap_srt(tmp_path):
    out = tmp_path / "x.gaps.srt"
    core.write_gap_srt(out, [(5.0, 7.0, -3.0)])
    text = out.read_text(encoding="utf-8")
    assert "00:00:05,000 --> 00:00:07,000" in text and "-3 dB" in text
