# Changelog

All notable changes to WhisperX Subtitle Workbench. Versioning follows [Semantic Versioning](https://semver.org): MAJOR.MINOR.PATCH.

## [0.2.0] – 2026-11-03

**Updating from 0.1.0:** close the old window first. Your `whisper_tool.ini` stays as it is, so the new options are off. Switch them on under *Settings → Subtitles* (recommended: min. display time 1, reading speed 17, balance lines on). Python packages need no update.

- Sentence mode: minimum display time (`[Layout] min_duration`) and maximum reading speed (`[Layout] max_cps`, characters per second). Only the end of a subtitle moves into the pause after it – never into the next subtitle, never beyond `max_duration`; start and text stay unchanged. Subtitles that are still too fast are listed in the log. Word by word, raw and lyrics are not affected. Both are off (0) in an existing `whisper_tool.ini`; the template recommends 1 s and 17 characters/s
- Sentence mode: line balancing (`[Layout] balance_lines`). The words of a subtitle are spread evenly over its lines instead of filling the first line to the limit; a break after a comma is preferred, an article/preposition at the end of a line is avoided. Same words and same number of lines, so the times do not change. Off in an existing `whisper_tool.ini`, on in the template
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
