# Changelog

All notable changes to WhisperX Subtitle Workbench. Versioning follows [Semantic Versioning](https://semver.org): MAJOR.MINOR.PATCH.

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
