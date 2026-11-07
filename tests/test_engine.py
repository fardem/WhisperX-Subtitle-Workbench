"""Engine: cancel during a step, retries on full graphics memory, lyrics emissions in passes."""

import contextlib
import sys

import numpy as np
import pytest

import wt_core as core
from helpers import ROOT


def engine():
    return core.Engine(core.Config(ROOT / "whisper_tool.example.ini"), log=lambda m: None)


def test_cancel_takes_effect_in_the_progress_callback():
    eng, seen = engine(), []
    eng.progress = lambda stage, pct: seen.append((stage, pct))
    report = eng._step("asr")
    report(10.0)
    eng.cancel.set()
    with pytest.raises(core.Cancelled):
        report(20.0)
    assert seen == [("asr", 10.0)]


def oom():
    raise RuntimeError("CUDA out of memory. Tried to allocate 12.00 GiB")


def test_out_of_memory_twice_runs_the_fallback():
    eng, unloads = engine(), []
    eng.unload = lambda keep=(): unloads.append(keep)
    assert eng._oom_retry(oom, keep=("mms",), fallback=lambda: "passes") == "passes"
    assert unloads == [("mms",), ("mms",)]


def test_out_of_memory_without_fallback_raises_after_one_retry():
    eng, calls = engine(), []
    eng.unload = lambda keep=(): None
    def fn():
        calls.append(1)
        oom()
    with pytest.raises(RuntimeError, match="out of memory"):
        eng._oom_retry(fn, keep=())
    assert len(calls) == 2


def test_other_errors_and_cancel_are_not_retried():
    eng, calls = engine(), []
    def fn():
        calls.append(1)
        raise core.Cancelled()
    with pytest.raises(core.Cancelled):
        eng._oom_retry(fn, keep=(), fallback=lambda: "passes")
    assert calls == [1]


def conv_frames(x):
    """Frames of a model without attention: window of 400 samples, hop of 320, like the conv stack of MMS_FA."""
    n = max((len(x) - 400) // core.MMS_HOP + 1, 0)
    cs = np.concatenate([[0.0], np.cumsum(x, dtype=np.float64)])
    k = np.arange(n) * core.MMS_HOP
    return cs[k + 400] - cs[k]


@pytest.mark.parametrize("extra", [-1, 0, 50, 100, 79 + core.LYRICS_CHUNK * core.SAMPLE_RATE, 12345])
def test_passes_give_the_frames_of_one_pass(extra):
    n = 2 * core.LYRICS_CHUNK * core.SAMPLE_RATE + extra
    x = np.random.default_rng(1).random(n)
    passes = [conv_frames(x[lo:hi])[first:end] for lo, hi, first, end in core.emission_chunks(n)]
    assert np.allclose(np.concatenate(passes), conv_frames(x))


class Tensor(np.ndarray):
    def unsqueeze(self, dim):
        return np.expand_dims(self, dim).view(Tensor)

    def to(self, device):
        return self


def lyrics_engine(monkeypatch, seen):
    fake_torch = type(sys)("torch")
    fake_torch.from_numpy = lambda a: a.view(Tensor)
    fake_torch.inference_mode = contextlib.nullcontext
    fake_torch.cat = lambda parts, dim: np.concatenate(parts, axis=dim)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    model = lambda wav: (conv_frames(np.asarray(wav[0]))[None, :, None], None)
    def aligner(emission, tokens):
        seen.append(np.asarray(emission))
        return [[Span(10 * i, 10 * i + 5)] for i in range(len(tokens))]
    eng = engine()
    eng._device, eng._uroman = "cpu", type("U", (), {"romanize_string": staticmethod(lambda w: w)})()
    eng._mms = (model, lambda words: words, aligner)
    return eng


class Span:
    def __init__(self, start, end):
        self.start, self.end = start, end


def test_lyrics_in_passes_give_the_cues_of_one_pass(monkeypatch):
    seen = []
    eng = lyrics_engine(monkeypatch, seen)
    audio = np.random.default_rng(2).random(3 * core.LYRICS_CHUNK * core.SAMPLE_RATE + 999).astype(np.float32)
    lines = ["one two", "three"]
    assert eng.align_lyrics(audio, lines, 0.0, chunked=True) == eng.align_lyrics(audio, lines, 0.0)
    assert seen[0].shape == seen[1].shape and np.allclose(seen[0], seen[1])
