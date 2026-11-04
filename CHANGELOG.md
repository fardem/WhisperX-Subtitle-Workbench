# Changelog

All notable changes to WhisperX Subtitle Workbench. Versioning follows [Semantic Versioning](https://semver.org): MAJOR.MINOR.PATCH.

## [0.4.1] – 2026-11-04

- Fixed: in *Raw* mode a WhisperX segment with a real pause inside stayed on screen during the whole pause (e.g. 4 seconds of applause). A real pause now starts a new segment in every mode: *Sentences* and *Raw* are split there, in *Word by word* every word is its own segment anyway. A segment without word times stays as it is
- The settings dialog and the INI template say so instead of naming only *Sentences*

## [0.4.0] – 2026-11-04

**Updating from 0.3.x:** close the old window first. The settings `split_pause`, `bridge_gaps`, `min_duration` and `max_cps` are gone – they are ignored and can be deleted from your `whisper_tool.ini`. The new values `pause`, `smooth` and `pause_hold` start with their defaults (2 seconds, on, 1 second); adjust them under *Settings → Subtitles → Pauses*. Python packages need no update.

- **One pause rule instead of five settings.** Only the length of a pause counts – in the middle of a sentence or between two sentences. A *segment* is what is shown: a word in *Word by word*, a subtitle in *Sentences*.
  - A speech pause longer than `pause` seconds is a **real pause**. In *Sentences* mode a new subtitle always begins after it (this replaces *new subtitle after a speech pause*).
  - With **smooth display** (`smooth`, default on): up to `pause` seconds the segment stays until the next one starts – end and next start are exactly the same, so the subtitle track of DaVinci Resolve never flickers. After a real pause the segment stays `pause_hold` seconds more, then the screen is empty until speech starts again. Starts never move.
  - Without the tick every segment ends exactly when the speech ends.
  - The hold time must be shorter than the pause limit, otherwise a real pause would never be visible; the settings dialog and the INI check this.
  - Applies to *Sentences*, *Word by word* and *Raw*. Lyrics keep their own rules (*hold line*, *gap to the next song line*).
- *Show at least* and *Leave reading time* are gone – the pause rule keeps a segment on screen until the next one or for the hold time. Subtitles with too much text for their time (more than 17 characters per second) are still listed in the log.
- *Settings → Subtitles*: *1 · Splitting*, *2 · Pauses*, *3 · Lyrics*; the INI template follows the same order

## [0.3.2] – 2026-11-03

**Updating from 0.3.1:** nothing to do – the INI format is unchanged. If you used *Close gaps* for lyrics in DaVinci Resolve, set *Gap to the next song line* to 0 instead.

- *Settings → Subtitles* is ordered by the two questions the tool answers one after the other, so no option overlaps with another:
  - **1 · Splitting** – which words go together into one subtitle ("Sentences" only): speech pause, long sentences, short words after a comma, line balancing
  - **2 · Display time** – how long a subtitle stays on screen: show at least, reading time, keep on screen until the next subtitle; an explanation says how they work together
  - **3 · Lyrics** – own rules for song lines; *hold line* stays in the main window
- *Max. display time* was really a rule for splitting long sentences. It is now *Split long sentences: at most X seconds of speech per subtitle* under *Splitting* and no longer cuts the display time
- *Close gaps* is now *Keep on screen until the next subtitle* under *Display time* and no longer applies to lyrics – it silently overrode *Gap to the next song line*. Lyrics keep their own rules; with a gap of 0 the lines follow each other seamlessly
- *Orphan* rows are now *Move short words after a comma into the next subtitle*, with their two numbers below the tick box
- Mode names as in the main window (*Sentences*, *Word by word*, *Raw*); the INI template comments follow the same order

## [0.3.1] – 2026-11-03

- *Settings → Subtitles* is grouped: *Splitting subtitles*, *Readability (sentence mode only)*, *Flicker-free, e.g. for DaVinci Resolve (all modes)* and *Lyrics*
- Options that used to be off with "0" now have a tick box: without the tick the number field is grey and shows the recommended value, with the tick it can be edited and counts. In the INI nothing changes (0 = off as before)
- Clearer texts: every row reads as a sentence with its unit written out ("Close gaps when the pause is shorter than [2] seconds"); the recommendation for DaVinci Resolve is now one number (2 seconds) instead of "2–3"
- The settings window is a little taller, so the buttons are never cut off

## [0.3.0] – 2026-11-03

**Updating from 0.2.0:** close the old window first. The new option is off in your `whisper_tool.ini`; for DaVinci Resolve tick *Settings → Subtitles → Close gaps* (pauses shorter than 2 seconds). Python packages need no update.

- Flicker-free subtitles, e.g. for DaVinci Resolve (`[Layout] bridge_gaps`, *Settings → Subtitles → Close gaps*): if the gap to the next subtitle is shorter than the set value, the subtitle stays until the next one starts – end and next start are exactly the same, so the subtitle track never shows an empty moment. Overlaps are cut. Longer pauses stay empty, starts never move. All modes, including word by word and lyrics. Off by default (recommended for DaVinci Resolve: 2 seconds)

## [0.2.0] – 2026-11-03

**Updating from 0.1.0:** close the old window first. Your `whisper_tool.ini` stays as it is, so the new options are off. Switch them on under *Settings → Subtitles* (recommended: min. display time 1, reading speed 17, balance lines on). The abbreviation rule works right away. Python packages need no update.

- Sentence mode: minimum display time (`[Layout] min_duration`) and maximum reading speed (`[Layout] max_cps`, characters per second). Only the end of a subtitle moves into the pause after it – never into the next subtitle, never beyond `max_duration`; start and text stay unchanged. Subtitles that are still too fast are listed in the log. Word by word, raw and lyrics are not affected. Both are off (0) in an existing `whisper_tool.ini`; the template recommends 1 s and 17 characters/s
- Sentence mode: line balancing (`[Layout] balance_lines`). The words of a subtitle are spread evenly over its lines instead of filling the first line to the limit; a break after a comma is preferred, an article/preposition at the end of a line is avoided. Same words and same number of lines, so the times do not change. Off in an existing `whisper_tool.ini`, on in the template
- Sentence mode: abbreviations stay with the next word and are no sentence end (`[Abbreviations]`, editable per language under *Settings → Languages*): "Dr. Müller", "z. B. gestern", "ca. acht Uhr" are never split into two lines or two subtitles – before, "Dr." ended the subtitle. Ordinal numbers in German and Turkish stay with the next word too ("6. November"). Works with an existing `whisper_tool.ini`: a language without an entry uses the built-in list
- Fixed: the dangling word rule could make the next subtitle longer than characters/line or lines/subtitle allow. The rule now applies while the subtitles are built, so every following subtitle keeps all limits
- `constraints.txt`: exact versions of all ~110 packages of the tested installation; step 5 now installs with `-c constraints.txt`, so a new installation matches the tested one even after new package releases
- README: installation guide checked against the package data – which PyTorch build for which graphics card (GTX 10xx and older need cu126), Python 3.10.11 as the last 3.10 with a Windows installer, a warning against installing into an existing venv, `nvidia-smi` driver check, and safer backup and update steps
- Automatic tests (`tests/`, pytest) for subtitle rules, reading time, gap check, comparison, INI and language files; they run on GitHub with every push (Windows and Linux)

## [0.1.0] – 2026-11-01

First public version.

- Speech → SRT with WhisperX: sentences, word by word or raw; per-language prompt, filler words, dangling words, replacements and optional per-language model
- Lyrics alignment with MMS_FA + uroman (`<name>.lyric.txt` next to the media file)
- JSON → SRT: rebuild subtitles from a saved WhisperX result
- Gap check: loud, voice-like passages without text, with a subtitle file to jump to them
- Comparison report (HTML): A ↔ B, A ↔ reference, A ↔ B ↔ reference – verdicts, error rates, shared errors, suggested replacements, timing deviation, clickable audio; colors only for A (blue), B (purple) and reference (green)
- System check on every start; settings dialog that keeps INI comments; drag & drop; one window for all starts (only within the same version)
- Interface, log and report in English, German and Turkish (`lang/*.json`), switchable under Settings → General
- Version shown in the title bar, the first log line, the system check, Settings → General and the report footer; `--version` on the command line
