# Changelog

All notable changes to WhisperX Subtitle Workbench. Versioning follows [Semantic Versioning](https://semver.org): MAJOR.MINOR.PATCH.

## [0.6.2] – 2026-11-05

**Updating from 0.6.1:** install the plugin again under *Settings → DaVinci Resolve*. The key `last_options` in `[Resolve]` is no longer used and can be deleted.

- **Jobs from DaVinci Resolve use the INI.** Language, mode and audio filter come from the plugin window (it remembers these three); all other settings come from the INI – prompt, characters per line and lines, and all rules from *Settings → Subtitles*. The options of the main window no longer apply to these jobs, and the workbench no longer brings back the options of the last DaVinci job. The log shows what the plugin chose and what comes from the INI. In the plugin, “as in the Workbench” is now called “Workbench default”
- **Fixed:** the plugin window did not come back at its saved size. Resolve 21.2 reports the width a little below the minimum the plugin sets (505 instead of 520), and the plugin dropped such a size as unusable
- Tested with DaVinci Resolve Studio 21.2.0.5

## [0.6.1] – 2026-11-05

**Updating from 0.6.0:** install the plugin again under *Settings → DaVinci Resolve*. The plugin window starts once with its new, smaller size.

- **Fixed: Resolve placed old subtitles.** Every hand-over used the same file name (`<Timeline>_<LANG>.srt`). Resolve does not read a file again that is already in its media pool – it placed the subtitles of the first import, e.g. word by word although the new SRT has sentences. The SRT now gets a new name every time (`<Timeline>_<LANG>_<date-time>.srt`). *Import SRT …* imports a copy with a new name, so a corrected file is read again
- The plugin compares the number of subtitles on the track with the SRT and reports a difference
- **Audio filter** as a tick in the plugin window – it goes to the workbench with the job and wins over the main window (off at first, like in the INI template)
- The plugin window starts smaller (600 × 330) and can be made smaller than before. Language, mode and audio filter are saved at once when changed, not only on *Close*

## [0.6.0] – 2026-11-05

**Updating from 0.5.x:** close the workbench, update, then install the plugin again under *Settings → DaVinci Resolve*. The new options in `[Resolve]` start with their defaults (automatic hand-over off, extra tracks on, minimize after the hand-over).

- **Plugin window:**
  - shows where the job stands in the workbench: waiting for the start, the countdown, loading, transcription and word timestamps in percent, done, error (the workbench writes `progress.txt` into the job folder)
  - **Language** and **Mode** can be chosen in the plugin; “as in the Workbench” keeps the options of the workbench window. A choice here wins over the workbench for this job
  - remembers its position, its size and the two choices (`resolve_temp\plugin.txt`)
- **Gaps and too fast subtitles as own subtitle tracks** (*Settings → DaVinci Resolve*, on by default): “WhisperX gaps” marks loud passages without text from the gap check, “WhisperX too fast” marks subtitles with more than 17 characters per second (mode *Sentences*). They are for checking in Resolve – delete the tracks afterwards. The transcript track stays the active one; nothing is added when there is nothing to show
- **Hand over by itself** (*Settings → DaVinci Resolve*, off by default): as soon as the job is transcribed, the subtitles go to the waiting plugin
- **After the hand-over** the workbench is minimized (the models stay loaded) or – with the new tick – closed. While other files are still running it is always minimized

## [0.5.1] – 2026-11-05

**Updating from 0.5.0:** close the workbench, update, then install the plugin again under *Settings → DaVinci Resolve* (the tab shows “please update”). The old folder `temp\resolve\` is removed on the next start.

- **DaVinci Resolve plugin window:**
  - it no longer starts cut off or squeezed: the layout is calculated again after the window appears, the window has a minimum size, and the hint about timeline changes and the questions are part of the status text instead of lines that are shown later (they overlapped the buttons)
  - after the import the window comes to the front with the result and stays there until *OK* (the *Close* button says *OK* then)
- **Workbench:** **→ Hand over to DaVinci** is blue and bold once the job is transcribed. After the hand-over the window minimizes itself; it stays open, so the models stay loaded for the next job
- One folder level instead of three: job folders and the SRT of the last hand-over are in `resolve_temp\` (before: `temp\resolve\` and `temp\resolve\letzte\`). Only one SRT lies there; job folders next to it are not touched

## [0.5.0] – 2026-11-05

**Updating from 0.4.x:** close the old window first. Python packages need no update, and your `whisper_tool.ini` needs no change: without the new section `[Resolve]` its defaults apply (countdown 10 seconds), the tool adds it when needed. To work with DaVinci Resolve, install the plugin under *Settings → DaVinci Resolve*.

- **DaVinci Resolve plugin** (`resolve/WhisperX_Subtitles.lua`, Resolve Studio, *Workspace → Scripts → WhisperX_Subtitles* on every page) – subtitles on a **native subtitle track**, not as Text+ titles:
  - *Transcribe* renders the main mix of the timeline as WAV – In to Out if set, otherwise the whole timeline – into `temp\resolve\<job>\` of the workbench and starts the workbench with it (`pythonw.exe`, no console window; an open workbench takes the file over and keeps its models loaded)
  - the render job is deleted afterwards; the page, the Deliver settings (saved as a temporary render preset) and the In/Out marks are restored – also after an error or *Cancel*. Left-over render jobs and presets of the plugin are removed before a new render, jobs of others are never touched
  - frames come from the API, never from timecode strings: start frame, start timecode, In/Out and end frame go into the job file (`key=value`), whatever the start of the timeline is
  - after the hand-over it switches back to the timeline of the job (by its unique ID), asks if the end of the timeline has changed, imports the SRT into the media pool bin *WhisperX* and puts it onto the first empty subtitle track, otherwise a new one: all other subtitle tracks are deactivated, the target is activated – the way Resolve 21.1.2 accepts an SRT. It checks that the clips arrived and that the first subtitle is at the expected frame, names the track `WhisperX <LANG>` (`WhisperX DE 2` if the name exists) and deletes the WAV and the job folder
  - *Import SRT …* places any SRT the same way, e.g. a translation `…_EN.srt` (the language comes from the file name, otherwise the plugin asks)
  - every step is logged under *Workspace → Console*
- **Resolve mode** in the workbench – only for files with the job file of the plugin next to them, without it nothing changes:
  - the job is listed as “*Timeline* · DaVinci Resolve” and has its own countdown, independent of the normal one; when the plugin opened the window, it is transcribed with the options of the last DaVinci job
  - **→ Hand over to DaVinci** shifts the SRT by the offset of the job and keeps it as the only file in `temp\resolve\letzte\<Timeline>_<LANG>.srt` (the previous one is deleted only after the new one is written), then tells the plugin through a result file that is written atomically
  - job folders that were cancelled, imported or left behind are removed on the next start
- New tab *Settings → DaVinci Resolve*: install / update the plugin – the folder of the workbench, the interface language and all texts of the plugin (from `lang/*.json`) are written into it –, whether it is installed and in which version, countdown for DaVinci jobs (`[Resolve] countdown`, 0 = off). The installer refuses a workbench folder with non-ASCII characters, which Resolve's Lua cannot open
- `resolve/tools/probe_subtitles.lua`: the test script that checks how Resolve places an SRT on a subtitle track – run it again after a Resolve update
- Tests for the SRT shift (25 and 29.97 fps, drop frame), the job and result files, the Resolve mode and the rule “only one last SRT”. The plugin itself is tested against a fake Resolve when the package `lupa` is installed
- Fixed: a second countdown no longer made the first one tick twice as fast
- README in all three languages: every screenshot taken again with 0.5.0, new ones of *Settings → DaVinci Resolve*, a DaVinci job ready to hand over and *Settings → Subtitles*; the plugin is named in the introduction and in the usage table

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
