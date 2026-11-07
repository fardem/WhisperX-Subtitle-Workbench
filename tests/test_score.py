"""Score check: words that fit the audio badly (the "score" of whisperx.align) – no GPU needed."""

import json

import pytest

import wt_core as core
import wt_i18n as i18n
from helpers import ROOT


def w(word, start, end, score=None):
    d = {"word": word, "start": start, "end": end}
    if score is not None:
        d["score"] = score
    return d


def result(*words, language="de"):
    """WhisperX result with the given words in one segment."""
    words = list(words)
    return {"segments": [{"start": words[0]["start"] if words else 0, "end": words[-1]["end"] if words else 0,
                          "text": " ".join(x["word"] for x in words), "words": words}], "language": language}


# ─── which words count ────────────────────────────────────────────────────────

def test_only_words_below_the_threshold_are_marked():
    r = result(w("Bundestag", 1.0, 1.6, 0.39), w("heute", 3.0, 3.4, 0.40), w("Rafah", 6.0, 6.5, 0.12),
               w("klar", 9.0, 9.3, 0.95))
    found = core.find_unsure(r, 0.4)
    assert [words for _, _, words in found] == [[("Bundestag", 0.39)], [("Rafah", 0.12)]]   # 0.40 is not below
    assert [words for _, _, words in core.find_unsure(r, 0.96)][-1] == [("klar", 0.95)]


def test_words_without_score_or_with_fewer_than_3_letters_are_skipped():
    r = result(w("2024", 1.0, 1.5),                       # numbers get no score from the alignment
               w("in", 2.0, 2.1, 0.01), w("z.B.", 3.0, 3.3, 0.02), w("42.", 4.0, 4.2, 0.03),
               w("Haus", 5.0, 5.3), w("nan", 6.0, 6.3, float("nan")),
               w("Tür", 8.0, 8.3, 0.05))                  # 3 letters (umlaut too) count
    assert core.find_unsure(r, 0.95) == [(8.0, 8.5, [("Tür", 0.05)])]
    assert core.word_score({"word": "ab", "score": 0.1}) is None
    assert core.word_score({"word": "ab.c", "score": 0.1}) == 0.1     # letters are counted, not characters


def test_punctuation_is_removed_from_the_word():
    found = core.find_unsure(result(w("„Rafah,", 1.0, 1.6, 0.2)), 0.4)
    assert found[0][2] == [("Rafah", 0.2)]


def test_words_without_times_are_skipped():
    r = result(w("Gaza", 1.0, 1.6, 0.2))
    r["segments"][0]["words"].append({"word": "Rafah", "score": 0.1})
    assert core.find_unsure(r, 0.4) == [(1.0, 1.6, [("Gaza", 0.2)])]


# ─── entries ──────────────────────────────────────────────────────────────────

def test_words_at_most_one_second_apart_become_one_entry():
    r = result(w("Erster", 1.0, 1.5, 0.2), w("Zweiter", 2.5, 2.7, 0.3),      # exactly 1.0 s apart → joined
               w("Dritter", 3.71, 4.0, 0.1))                                  # 1.01 s apart → own entry
    found = core.find_unsure(r, 0.4)
    assert [(s, e) for s, e, _ in found] == [(1.0, 2.7), (3.71, pytest.approx(4.21))]
    assert found[0][2] == [("Erster", 0.2), ("Zweiter", 0.3)]
    assert len(core.find_unsure(result(w("Erster", 1.0, 1.3, 0.2), w("Zweiter", 2.3, 2.7, 0.3)), 0.4)) == 1


def test_good_words_in_between_do_not_split_an_entry():
    # only the distance between the marked words counts
    r = result(w("Erster", 1.0, 1.3, 0.2), w("gut", 1.4, 1.6, 0.9), w("Zweiter", 2.0, 2.4, 0.3))
    assert [(s, e) for s, e, _ in core.find_unsure(r, 0.4)] == [(1.0, 2.4)]


def test_entry_starts_exactly_at_the_first_word_and_lasts_at_least_half_a_second():
    found = core.find_unsure(result(w("Rafah", 12.345, 12.5, 0.2)), 0.4)
    assert found == [(12.345, pytest.approx(12.845), [("Rafah", 0.2)])]
    long_word = core.find_unsure(result(w("Bundesverfassungsgericht", 1.0, 2.2, 0.3)), 0.4)
    assert long_word[0][:2] == (1.0, 2.2)                  # longer words keep their end


def test_entries_never_overlap():
    words, t = [], 0.0
    for i in range(200):                                   # dense and sparse marks mixed
        words.append(w(f"Wort{i}", t, t + 0.1, 0.1 if i % 3 else 0.9))
        t += 0.35 if i % 7 else 1.6
    found = core.find_unsure(result(*words), 0.4)
    assert len(found) > 10
    for (s1, e1, _), (s2, e2, _) in zip(found, found[1:]):
        assert s1 < e1 <= s2 < e2


def test_no_scores_at_all():
    plain = result(w("Haus", 1.0, 1.3), w("Baum", 2.0, 2.3))       # language without alignment model
    assert core.find_unsure(plain, 0.4) == [] and core.score_stats(plain) is None
    assert core.score_stats({"segments": []}) is None


# ─── text of an entry ─────────────────────────────────────────────────────────

def test_at_most_four_words_then_plus_n():
    words = [("eins", 0.11), ("zwei", 0.22), ("drei", 0.33), ("vier", 0.25), ("fünf", 0.15), ("sechs", 0.3)]
    assert core.unsure_words(words) == "eins 0.11 · zwei 0.22 · drei 0.33 · vier 0.25 +2"
    assert core.unsure_words(words[:4]) == "eins 0.11 · zwei 0.22 · drei 0.33 · vier 0.25"
    assert core.unsure_label(words[:1]) == "unsure: eins 0.11"


@pytest.mark.parametrize("code, label", [("de", "unsicher: Bundesverfassungsgericht 0,32 · Rafah 0,28"),
                                         ("en", "unsure: Bundesverfassungsgericht 0.32 · Rafah 0.28"),
                                         ("tr", "belirsiz: Bundesverfassungsgericht 0,32 · Rafah 0,28")])
def test_label_uses_the_decimal_sign_of_the_language(code, label):
    i18n.load(code)
    try:
        assert core.unsure_label([("Bundesverfassungsgericht", 0.32), ("Rafah", 0.28)]) == label
    finally:
        i18n.load("en")


def test_score_srt(tmp_path):
    out = tmp_path / "x.unsure.srt"
    core.write_score_srt(out, [(5.0, 5.5, [("Rafah", 0.28)])])
    assert out.read_text(encoding="utf-8") == "1\n00:00:05,000 --> 00:00:05,500\nunsure: Rafah 0.28\n\n"


def test_score_file_names_of_every_language():
    assert i18n.all_values("core.score_suffix") == {".unsure.srt", ".unsicher.srt", ".belirsiz.srt"}


# ─── statistics ───────────────────────────────────────────────────────────────

def test_median_and_5_percent_quantile_of_the_rated_words():
    words = [w(f"Wort{i}", i, i + 0.3, s) for i, s in enumerate([0.1, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0])]
    words += [w("in", 20, 20.1, 0.0), w("2024", 21, 21.5)]          # not rated: they do not count
    median, low = core.score_stats(result(*words))
    assert median == pytest.approx(0.75)
    assert low == pytest.approx(0.1 + 0.35 * (0.5 - 0.1))           # numpy: linear between the two lowest


# ─── in the engine (JSON → SRT, no GPU) ───────────────────────────────────────

def make_json(tmp_path, *words, name="rede"):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(result(*words)), encoding="utf-8")
    return path


def make_ini(tmp_path, score="", write_srt=1):
    ini = tmp_path / "test.ini"
    ini.write_text("[Gaps]\nenabled = 1\n" + (f"[Score]\nthreshold = {score}\nwrite_srt = {write_srt}\n"
                                              if score != "" else ""), encoding="utf-8")
    return core.Config(ini)


WORDS = (w("Die", 1.0, 1.2, 0.95), w("Bundesregierung", 1.3, 2.0, 0.31), w("hat", 2.1, 2.3, 0.9),
         w("Rafah", 2.4, 2.8, 0.22), w("besucht.", 2.9, 3.4, 0.88))


def test_engine_reports_and_writes_the_score_file(tmp_path):
    log = []
    cfg = make_ini(tmp_path, 0.4)
    info = core.Engine(cfg, log=log.append).process(make_json(tmp_path, *WORDS), core.JobOptions.from_config(cfg))
    assert info["unsure"] == 1 and info["score_list"] == [(1.3, 2.8, [("Bundesregierung", 0.31), ("Rafah", 0.22)])]
    assert "gaps" not in info                                         # no audio → no gap check for JSON
    text = "\n".join(log)
    assert "Score: median 0.88 · 5% below 0.24 · 1 place below 0.40" in text
    assert "0:01.3 – 0:02.8   (Bundesregierung 0.31 · Rafah 0.22)" in text
    assert (tmp_path / "rede.unsure.srt").read_text(encoding="utf-8") == \
        "1\n00:00:01,300 --> 00:00:02,800\nunsure: Bundesregierung 0.31 · Rafah 0.22\n\n"


def test_the_subtitles_stay_exactly_the_same(tmp_path):
    src = make_json(tmp_path, *WORDS)
    texts = []
    for score in ("", 0.4, 0.95):                                     # off, on, almost every word marked
        cfg = make_ini(tmp_path, score)
        core.Engine(cfg, log=lambda s: None).process(src, core.JobOptions.from_config(cfg))
        texts.append((tmp_path / "rede.srt").read_text(encoding="utf-8"))
    assert texts[0] == texts[1] == texts[2] and "Bundesregierung" in texts[0]


def test_old_score_file_goes_when_nothing_is_found(tmp_path):
    old = tmp_path / "rede.unsure.srt"
    old.write_text("old", encoding="utf-8")
    log = []
    cfg = make_ini(tmp_path, 0.1)
    info = core.Engine(cfg, log=log.append).process(make_json(tmp_path, *WORDS), core.JobOptions.from_config(cfg))
    assert info["unsure"] == 0 and info["score_list"] == [] and not old.exists()
    assert any("✓ Score: median 0.88" in line and "0 places below 0.10" in line for line in log)


def test_without_write_srt_only_the_log(tmp_path):
    cfg = make_ini(tmp_path, 0.4, write_srt=0)
    info = core.Engine(cfg, log=lambda s: None).process(make_json(tmp_path, *WORDS), core.JobOptions.from_config(cfg))
    assert info["unsure"] == 1 and not (tmp_path / "rede.unsure.srt").exists()


def test_off_without_score_section(tmp_path):
    cfg = make_ini(tmp_path)
    info = core.Engine(cfg, log=lambda s: None).process(make_json(tmp_path, *WORDS), core.JobOptions.from_config(cfg))
    assert "unsure" not in info and "score_list" not in info and not (tmp_path / "rede.unsure.srt").exists()


def test_without_scores_one_info_line(tmp_path):
    old = tmp_path / "plain.unsure.srt"
    old.write_text("old", encoding="utf-8")
    log = []
    cfg = make_ini(tmp_path, 0.4)
    src = make_json(tmp_path, w("Haus", 1.0, 1.3), w("Baum", 2.0, 2.3), name="plain")
    info = core.Engine(cfg, log=log.append).process(src, core.JobOptions.from_config(cfg))
    assert info["unsure"] == 0
    score_lines = [line for line in log if "core.score" in line or "Score" in line or "unsure" in line]
    assert score_lines == [i18n.line("info", "core.score_none")]
    assert old.exists()                                              # nothing else happens


def test_example_ini_switches_the_score_check_on():
    cfg = core.Config(ROOT / "whisper_tool.example.ini")
    assert (cfg.score_threshold, cfg.score_srt) == (0.4, True)
