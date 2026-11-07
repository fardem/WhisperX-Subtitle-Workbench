# WhisperX Subtitle Workbench

**English** · [Deutsch](README.de.md) · [Türkçe](README.tr.md)

**Version 0.7.0** · [Changelog](CHANGELOG.md)

A Windows desktop tool for **subtitles from speeches** (WhisperX), **lyrics aligned to songs** (MMS_FA) and **comparing transcripts word by word** – with a **plugin for DaVinci Resolve** and a user interface in English, German and Turkish.

![Main window](docs/images/main-window.png)

You drop video or audio files into the window, and the tool creates `.srt` subtitles. The subtitle rules are tuned for readable output: line length, orphan words, dangling words, long sentences and pauses. A gap check finds loud passages that got no text, a score check finds words that fit the audio badly. The comparison report shows which model or setting recognizes better, with error rates, timing deviations and clickable audio. The DaVinci Resolve plugin transcribes a timeline from inside Resolve and puts the subtitles onto a subtitle track.

---

## Contents

- [Features](#features)
- [Requirements and dependencies](#requirements-and-dependencies) ← **read this before installing**
- [Installation (Windows)](#installation-windows)
- [Updating safely](#updating-safely)
- [Usage](#usage)
- [Comparing transcripts](#comparing-transcripts)
- [DaVinci Resolve](#davinci-resolve)
- [Settings](#settings)
- [Interface language and translations](#interface-language-and-translations)
- [Command line](#command-line)
- [Troubleshooting](#troubleshooting)
- [Project structure](#project-structure)
- [License and credits](#license-and-credits)

---

## Features

- **Speech → SRT** with WhisperX (faster-whisper + word timestamps). It keeps the models loaded between files, so the second file starts without loading time.
- **Three subtitle modes:**
  - **Sentences:** full sentences with a character limit and rules for orphans, dangling words, pauses and long sentences.
    Subtitles with too much text for their time (more than 17 characters per second) are listed in the log.
    Line balancing spreads the words evenly over the lines (`we did not / see that coming` instead of `we did not see that / coming`), with a break after a comma where possible.
    Abbreviations and dates stay together: `Dr. Smith`, `e.g. Monday` and (in German) `6. November` are never split into two lines or two subtitles. The list per language is under *Settings → Languages*.
  - **Word by word:** one cue per word, e.g. for karaoke effects.
  - **Raw:** the WhisperX segments unchanged – only split at a real pause.
- **Pauses, flicker-free for DaVinci Resolve** (*Settings → Subtitles → Pauses*): only the length of a pause counts. Up to the set limit (e.g. 2 seconds) a segment stays until the next one starts, so the subtitle track of DaVinci never shows an empty moment. After a longer, real pause (applause, music) it stays for a short hold time, then the screen is empty. Starts stay exact. Works for Sentences, Word by word and Raw; for lyrics set *Gap to the next song line* to 0.
- **Language per file:** the language is detected per file, or you set it. Each language has its own prompt, filler words, dangling words, replacements and optionally its own model (for example a Turkish fine-tune).
- **Lyrics mode:** put `song.lyric.txt` next to `song.mp3`, and every line of the song text is aligned to the audio. This uses forced alignment (MMS_FA + uroman) and works for any language.
- **JSON → SRT:** you can keep the WhisperX result and later rebuild the subtitles with a different layout in seconds.
- **Gap check:** after each transcription it finds loud, voice-like passages without text, i.e. speech that was missed. It writes an extra subtitle file you can load to jump to each passage.
- **Score check:** the word timestamps give every word a score from 0 to 1 – how well it fits the audio at its place. Words below a threshold (recommended 0.40) were probably misrecognized or misplaced; the check lists them in the log and in an extra subtitle file. The subtitles themselves stay unchanged.
- **Comparison report** (HTML): A vs. B, A vs. reference, or A vs. B vs. reference.
  - Shows who is right at every difference.
  - Shows the error rate (WER) of each file and the errors both files share.
  - Suggests replacements.
  - Shows timing deviations.
  - Every time in the report is clickable and plays the audio from that point.
- **DaVinci Resolve:** a plugin transcribes the timeline from inside Resolve; the subtitles come back onto a native subtitle track (see [DaVinci Resolve](#davinci-resolve)).
- **System check** on every start: it lists Python, all packages with versions, ffmpeg, the models and the graphics card, and tells you which feature would fail.
- **Comfort:**
  - Drag & drop onto the BAT file or into the window. A second start hands its files to the open window.
  - A countdown starts processing automatically; jobs from DaVinci Resolve have their own.
  - Recognized lines appear live in the log.
  - Tooltips explain every option.
  - A settings dialog writes only the changed values and keeps the comments in the INI.
- **Multilingual:** English, German and Turkish. You can add more languages by copying a single JSON file (see [Interface language and translations](#interface-language-and-translations)).

---

## Requirements and dependencies

> **The most important part of this README.** WhisperX sits on top of PyTorch, CTranslate2, pyannote and several smaller packages, and they depend on each other's **exact** versions. One careless `pip install` can quietly replace your CUDA build of PyTorch with a CPU build or a version that does not match. The tool then fails in ways that are hard to understand. Follow the order below and you will get a combination that is known to work.

### What you need

| What | Tested with | Notes |
|---|---|---|
| Windows | Windows 10/11, 64-bit | The UI is Tkinter; the core also runs on Linux, but the BAT starter is for Windows |
| NVIDIA graphics card | RTX 5080 (16 GB) | ≥ 8 GB VRAM recommended for `large-v3`. RTX 20xx or newer; GTX 10xx and older need a different PyTorch build (step 4). CPU works, but is very slow |
| NVIDIA driver | R570 or newer | Needed for CUDA 12.8 wheels. Only the driver is required; you do **not** need to install the CUDA toolkit |
| Python | **3.10.11** (3.10–3.13 allowed by WhisperX) | From python.org, with “tcl/tk and IDLE” (Tkinter), which is ticked by default. 3.10.11 is the last 3.10 version with a Windows installer. Python 3.10 has had no security updates since October 2026; the package set is tested with 3.10 |
| ffmpeg | 6.x / 7.x | A separate program, not a pip package. Any full build, e.g. `winget install Gyan.FFmpeg` |
| Internet | first run only | Whisper model (~3 GB), alignment models per language, MMS_FA (~1.2 GB for lyrics) |

No Hugging Face token is needed, because the tool does not use speaker diarization.

### The tested package set

These versions run together in production (Python 3.10, Windows 11, RTX 5080):

| Package | Version | Why exactly this one |
|---|---|---|
| `torch` | **2.8.0+cu128** | WhisperX 3.8 requires `torch~=2.8.0` (2.8.x only). **Must** come from the PyTorch CUDA index; the PyPI wheel for Windows is CPU-only. RTX 50xx (Blackwell, sm_120) needs a cu128 build or newer |
| `torchaudio` | **2.8.0+cu128** | Must be **exactly** the same version and CUDA build as torch. Used for lyrics (MMS_FA) |
| `torchvision` | 0.23.0 | Belongs to torch 2.8 (required by WhisperX) |
| `torchcodec` | 0.7.0 | WhisperX requires `<0.8`, pyannote-audio 4 requires `≥0.7`, so only 0.7.x fits. It belongs to torch 2.8 |
| `whisperx` | **3.8.5** | The tool is tested with 3.8.x; the system check warns below 3.8 |
| `faster-whisper` | 1.2.1 | Requires `ctranslate2 <5` |
| `ctranslate2` | 4.7.1 | Versions **≥ 4.5** use CUDA 12 + **cuDNN 9**, which torch 2.8 brings along. Versions ≤ 4.4 expect cuDNN 8 and fail |
| `pyannote-audio` | 4.0.4 | Speech detection (VAD). Requires torch ≥ 2.8 and torchcodec ≥ 0.7 |
| `numpy` | ≥ 2.1 (installed by WhisperX) | Packages that need `numpy<2` cannot live in the same venv |
| `uroman` | 1.3.1.1 | Lyrics mode only (romanizes text for the alignment) |
| `tkinterdnd2` | 0.6.3, *optional* | Drag & drop directly into the window. It has no dependencies; install it with `--no-deps` |

### How the dependencies are connected

```
torch 2.8.0+cu128 ──┬── torchaudio 2.8.0+cu128   (same version + same CUDA build, always)
                    ├── torchvision 0.23.0
                    ├── torchcodec 0.7.x          (WhisperX <0.8, pyannote ≥0.7)
                    └── cuDNN 9 (bundled) ─────── ctranslate2 ≥4.5 ── faster-whisper 1.2.x
                                                                    │
whisperx 3.8.x ── requires torch~=2.8.0, torchaudio~=2.8.0, torchvision~=0.23.0,
                  torchcodec <0.8, ctranslate2 ≥4.5, faster-whisper ≥1.2, pyannote-audio ≥4, numpy ≥2.1
```

### Known ways to break the setup

| What happens | Typical symptom | Prevention / fix |
|---|---|---|
| `pip install whisperx` **before** torch | torch comes from PyPI as a CPU build → system check: “torch is a build without CUDA” | Always install torch first from the PyTorch index (step 4 below) |
| `pip install -U whisperx` or another package pulls a different torch | torch/torchaudio replaced, lyrics or VAD fail, CUDA gone | Run `pip install --dry-run …` first and abort if `torch`, `torchaudio`, `torchvision`, `torchcodec` or `ctranslate2` would change |
| torch and torchaudio from different versions or builds | Import errors in torchaudio (`DLL load failed`, `WinError 127`), lyrics fail; the system check warns | Reinstall both together with the same version and `--index-url` |
| `ctranslate2` < 4.5 | `Could not locate cudnn_ops_infer64_8.dll` | Use ctranslate2 ≥ 4.5 (tested: 4.7.1) |
| An older CUDA build (cu121/cu124) on an RTX 50xx | `no kernel image is available for execution on the device` | Use the cu128 build |
| The cu128 build on a GTX 10xx, GTX 9xx or Titan V | Warning “… CUDA capability sm_61 is not compatible …”, then `no kernel image is available` | Use the cu126 build (step 4) |
| `pip install -r requirements.txt` in an **existing** venv with a different torch | pip replaces that torch with the CPU build from PyPI, and the WhisperX that was installed there stops working too | Never install into an existing venv; use a new folder with its own venv |
| Another AI tool installed into the same venv | Different torch or `numpy<2` → various errors | Give other tools their own venv |
| Python without Tkinter | The tool reports “Tkinter is missing” | Python installer → Modify → “tcl/tk and IDLE” |

---

## Installation (Windows)

All commands are for `cmd.exe`. The example folder is `C:\WhisperX-Subtitle-Workbench`. You can use any other folder, as long as `venv` sits **inside** it, because `Whisper_Tool.bat` looks for it there.

**1. Install Python 3.10.11** from [python.org](https://www.python.org/downloads/release/python-31011/) (*Windows installer (64-bit)*). Newer 3.10 versions are only available as source code, without an installer.

- Keep “tcl/tk and IDLE” ticked.
- If you already use another Python, leave “Add python.exe to PATH” **unticked**. This guide does not need it (`py -3.10`), and your other Python stays the default.

**2. Install ffmpeg**, for example:

```bash
winget install Gyan.FFmpeg
```

Then open a **new** `cmd` window; the window you used for winget does not know the new path yet. You can also unzip a build to `C:\ffmpeg` and later enter `C:\ffmpeg\bin\ffmpeg.exe` under *Settings → Audio & output*.

**3. Get the workbench** with git, or download the ZIP from GitHub and unzip it:

```bash
git clone https://github.com/fardem/WhisperX-Subtitle-Workbench C:\WhisperX-Subtitle-Workbench
```

The ZIP unpacks into a folder called `WhisperX-Subtitle-Workbench-main`. Rename it to `WhisperX-Subtitle-Workbench`, or adjust the path in the commands below.

**4. Create the venv and install PyTorch with CUDA**. This step comes first and is the important one.

First check the driver. `nvidia-smi` shows the *Driver Version* in the top row; it should be 570 or higher:

```bash
nvidia-smi
```

```bash
cd /d C:\WhisperX-Subtitle-Workbench
```

```bash
py -3.10 -m venv venv
```

```bash
venv\Scripts\python.exe -m pip install --upgrade pip
```

```bash
venv\Scripts\python.exe -m pip install torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
```

> **Which build for which graphics card:**
> - RTX 50xx needs `cu128`.
> - RTX 20xx to 40xx work with `cu128`.
> - GTX 10xx, GTX 9xx and Titan V are **no longer supported** by the cu128 build. Use `cu126` instead of `cu128` at the end of the command above; step 6 then shows `2.8.0+cu126 12.6 True`. GTX 10xx cards also have no fast float16, so set *Settings → Whisper → Precision* to `int8` or `float32`.
>
> Only the cu128 build on an RTX 5080 is tested.

**5. Install WhisperX and the rest** with the tested versions:

```bash
venv\Scripts\python.exe -m pip install -r requirements.txt -c constraints.txt
```

`requirements.txt` says *what* gets installed. `constraints.txt` fixes **all** packages, including the ~100 that come along indirectly, to exactly the versions of the tested installation. A new installation therefore matches the tested one, even if a package releases a new version later. pip keeps the existing `torch 2.8.0+cu128`, because it already satisfies WhisperX's `torch~=2.8.0`.

**6. Check the installation:**

```bash
venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
```

The expected output is `2.8.0+cu128 12.8 True`. Then check the venv for conflicts:

```bash
venv\Scripts\python.exe -m pip check
```

**7. Optional: drag & drop directly into the window.** You can also do this later with one click under *Settings → General*:

```bash
venv\Scripts\python.exe -m pip install --no-deps tkinterdnd2==0.6.3
```

**8. Start** `Whisper_Tool.bat`. On the first start, the tool creates `whisper_tool.ini` from `whisper_tool.example.ini` and runs the system check. Every line of the check should be green.

**Desktop shortcut:** right-click `Whisper_Tool.bat` → *Create shortcut* → move the shortcut to the desktop. You can drop files and folders onto the shortcut.

### Using an existing WhisperX venv

If you already have a working WhisperX venv, e.g. in `C:\WhisperX\venv`, copy the files of this repository into `C:\WhisperX`, next to the `venv` folder. Nothing gets installed; the tool only uses the packages that are already there. Close the tool before copying, and extract the files directly into `C:\WhisperX`, not into a subfolder.

> **Do not run `pip install` in that venv, and especially not `requirements.txt`.** If its torch is not 2.8.x, pip replaces it with the CPU build from PyPI, and your existing WhisperX stops working as well. If the system check reports WhisperX as too old, install the workbench into a **new folder** with its own venv instead (steps 1–8). Your old installation stays untouched.

---

## Updating safely

- **Close the tool first.** The running version is shown in the title bar (e.g. *WhisperX Subtitle Workbench 0.7.0*), in the first log line and under *Settings → General*. If an older window is still open when you start the new version, the new one warns you instead of silently handing your files to the old window.
- **The workbench itself:** `git pull`, or copy the new files over the old ones. Your settings are in `whisper_tool.ini`. That file is not part of the repository, so an update never overwrites it. New options get default values automatically.
- **Python packages:** only update when you need something new. This is the safe way:

1. **Back up the venv:** close the tool and copy the whole `venv` folder, e.g. to `venv-backup`. This is the real backup. To restore it, delete `venv` and rename `venv-backup` back to `venv`. It only works at its original path.

2. Write down the current state, so you can look it up later:

   ```bash
   venv\Scripts\python.exe -m pip freeze > requirements-working.txt
   ```

   This list contains `torch==2.8.0+cu128`, which does not exist on PyPI. To install from it into a **new** venv, pip also needs the PyTorch index:

   ```bash
   venv\Scripts\python.exe -m pip install -r requirements-working.txt --extra-index-url https://download.pytorch.org/whl/cu128
   ```

3. Do a dry run first. It installs nothing:

   ```bash
   venv\Scripts\python.exe -m pip install --dry-run -U whisperx
   ```

4. If the dry-run output lists `torch`, `torchaudio`, `torchvision`, `torchcodec` or `ctranslate2` under “Would install”, **stop**. That update would break the combination.

---

## Usage

| You drop … | The tool does … | Result |
|---|---|---|
| a video/audio file (`.mp4 .mkv .mp3 .wav .m4a …`) | transcription with WhisperX | `<name>.srt` |
| a media file with `<name>.lyric.txt` next to it | lyrics alignment (one song line per line, UTF-8) | `<name>.lyric.srt` |
| a WhisperX `.json` (saved with “Keep JSON”) | rebuilds the subtitles only | `<name>.srt` |
| a folder | all media files in it | |
| the WAV of a DaVinci Resolve job – the plugin starts the tool with it | transcription, then **→ Hand over to DaVinci** | the subtitles on a subtitle track in Resolve |

- **Start** processes the selected files. If nothing is selected, it processes all files that are not done yet. If everything is done, it processes all files again, e.g. with another layout.
- **Language:**
  - *Automatic* detects the language per file, from the first 30 seconds.
  - A fixed language is more reliable. Choose it when the recording starts with music or an intro.
- **Prompt:** a short hint to Whisper with rare names and terms in the spelling you want. Write it in the language of the audio. The prompt for each language is set under *Settings → Languages*.
- **Gap check:**
  - Files with suspicious gaps get the status “⚠ gaps” in the list.
  - The passages appear in yellow in the log.
  - They are also saved as `<name>.gaps.srt`. The file name follows the interface language: `.luecken.srt` in German, `.bosluklar.srt` in Turkish. Load it as a second subtitle track in VLC or Subtitle Edit to jump to each passage.
- **Score check** (*Settings → Speech detection*, under the gap check; off in an INI from before 0.7.0):
  - The log first shows the median and the 5 % quantile of all word scores, e.g. `Score: median 0.88 · 5% below 0.61 · 7 places below 0.40`, then the places with their words and scores. Files with such places get the status “⚠ unsure”.
  - The places are also saved as `<name>.unsure.srt` (`.unsicher.srt` in German, `.belirsiz.srt` in Turkish), e.g. `unsure: Bundesverfassungsgericht 0.32 · Rafah 0.28`. Marked words at most 1 second apart are one entry.
  - A higher threshold gives more entries. Names and foreign words land below it more often, even when they are right. Numbers are practically never marked, and words with fewer than 3 letters are not rated.
  - It also works for a JSON saved with “Keep JSON”, not for lyrics. For a language without an alignment model there are no scores; the log says so.
- **Hover over any control** to get a detailed explanation.

---

## Comparing transcripts

Use **Compare …** in the file list to test models and settings. Only the plain text counts; timestamps and line breaks are ignored.

![Compare dialog](docs/images/compare-dialog.png)

| Files | What you learn |
|---|---|
| **A + B** | Where two transcripts differ, e.g. two models, beams or prompts |
| **A + reference** | All errors of A. The reference is an SRT you corrected by hand; 5–10 corrected minutes are enough |
| **A + B + reference** | Who is right at every difference, the error rate of both files, the errors both share, and suggested replacements |

The colors are the same everywhere: **A blue, B purple, reference green (correct)**. Nothing is struck through; *missing* marks a spot where a file has nothing.

![Comparison report](docs/images/report-overview.png)

![Differences with verdicts](docs/images/report-differences.png)

The report also contains:
- **Timing:** the typical deviation and the median offset, always shown. Passages where several words in a row are more than 1 s off are listed separately.
- **Errors in both:** errors that only a prompt or a replacement can fix.
- **Suggested replacements:** `wrong = right` lines with a copy button, ready for *Settings → Languages → Replacements*.
- **Spelling only:** differences in punctuation or upper/lower case, which do not count as errors.
- **Full text:** the whole text with all deviations marked.

**Listen:** if you add the video or audio, the tool creates a small audio track next to the report (`.m4a`). Every time in the report is then clickable and plays from 1.5 s before the passage. Keep the report and the `.m4a` file together.

Tip: set *Speech suffix* to `.{model}.srt` (*Settings → Audio & output*). Then every model writes its own SRT, which makes them easy to compare.

The report follows your system's light/dark mode:

![Dark mode](docs/images/report-dark.png)

---

## DaVinci Resolve

A plugin connects the workbench with **DaVinci Resolve Studio**: Resolve renders the audio of the timeline, the workbench transcribes it as usual, and the subtitles come back onto a **native subtitle track** – not as Text+ titles. You correct them right there in Resolve and style them further with your own tools. The plugin brings no title effects of its own.

### Installing the plugin

1. In the workbench: *Settings → DaVinci Resolve → Install / update plugin*. This writes `WhisperX_Subtitles.lua` to `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\`, so the plugin is listed on every page of Resolve. The folder of the workbench, the interface language and all texts of the plugin are written into the file – there is nothing to configure.
2. In Resolve: *Workspace → Scripts → WhisperX_Subtitles*. If it is not listed, restart Resolve once.

Install the plugin again after an update of the workbench, after moving its folder and after changing the interface language; the tab shows whether the installed plugin is up to date.

![Settings – DaVinci Resolve](docs/images/settings-resolve.png)

> The path of the workbench may only contain plain ASCII characters, e.g. `C:\WhisperX-Subtitle-Workbench` – no umlauts. On Windows, Resolve's Lua cannot open files under other paths; the installer says so instead of installing.

### Workflow

1. Open the timeline in Resolve. Set In and Out if only a part should be transcribed, otherwise the whole timeline is used. Start the plugin, choose **Language** and **Mode** if this job needs other ones than the workbench window (“Workbench default” takes the default from the INI), switch the **Audio filter** on or off, and press **Transcribe**. The plugin window remembers its position, its size and these three choices.
2. The plugin renders the main mix of the timeline as WAV into `resolve_temp\<job>\` of the workbench. Afterwards the render job is deleted, and the page, the Deliver settings and the In/Out marks are as before – also after an error or *Cancel*. Then it starts the workbench with the WAV; a workbench that is already open takes the file over and keeps its models loaded.
3. In the workbench the job appears as “*Timeline* · DaVinci Resolve”. It has its own countdown (*Settings → DaVinci Resolve*, 10 seconds by default). Language, mode and audio filter come from the plugin window; all other settings come from the INI: prompt, characters per line and lines, and all rules from *Settings → Subtitles*. The options of the main window do not apply to DaVinci jobs. The log shows both. Without the countdown, press *Start* yourself. Meanwhile the plugin window shows where the job stands: countdown, transcription in percent, done.
4. When the job is done, check the subtitles and press **→ Hand over to DaVinci** (blue once the job is done). The SRT is moved to the position of the range in the timeline and saved as `resolve_temp\<Timeline>_<LANG>_<date-time>.srt`; this folder keeps the SRTs of the last 10 hand-overs, and the 11th deletes the oldest. The name is new every time, because Resolve does not read a file again that is already in its media pool – it would place the subtitles of the first import. The workbench then minimizes itself – it stays open, so its models stay loaded for the next job. In *Settings → DaVinci Resolve* you can let it hand over by itself as soon as the job is transcribed, and close instead of minimize.
5. The plugin, still waiting in Resolve, switches back to the timeline of the job, imports the SRT into the media pool bin *WhisperX* and puts it onto the **first empty subtitle track** (ST1, ST2, …) – or onto a new one. The track is named `WhisperX DE` (`WhisperX DE 2` if the name exists) and stays the active subtitle track. Then the WAV and the job folder are deleted, and the plugin window comes to the front with the result until you press *OK*.
6. Up to three more subtitle tracks help with checking, each switched on or off in *Settings → DaVinci Resolve*: **WhisperX gaps** marks loud passages without text from the gap check, **WhisperX too fast** marks subtitles with more than 17 characters per second (mode *Sentences*), **WhisperX unsure** marks the words of the score check (whenever the score check is on – the row in *Settings → DaVinci Resolve* is the same setting as under *Speech detection*). Jump through them in Resolve, then delete the tracks. A track is only added when there is something to show, and the transcript track stays the active one.

![A DaVinci Resolve job in the workbench, ready to hand over](docs/images/resolve-job.png)

**Import SRT …** in the plugin puts any SRT onto a subtitle track the same way, e.g. a translation. It imports a copy with a new name from `resolve_temp\`, so a file corrected in the meantime is read again. The language for the track name comes from the file name (`…_EN.srt`), otherwise the plugin asks. The file dialog starts in `resolve_temp\`.

While the plugin waits, Resolve stays usable. Changes to the timeline in the transcribed range can make the subtitles no longer fit; if the end of the timeline has changed, the plugin asks before importing. Every step is logged under *Workspace → Console* (lines starting with `[WhisperX]`) – this output helps if something does not work.

### Known limits

- Tested with DaVinci Resolve **Studio** 21.1.2 and 21.2.0.5 on Windows. The free edition of Resolve 21.1 blocks file access for scripts; there only *Import SRT …* works.
- Resolve puts an SRT only onto the **active** subtitle track, and only one can be active. The plugin therefore deactivates the other subtitle tracks; afterwards the new track is the active one. An empty but locked track is skipped.
- The subtitles are placed relative to the start of the timeline, i.e. shifted by the In point. Whether the start timecode (e.g. 01:00:00:00) has to be added as well is still being tested; the plugin compares the position of the first subtitle with the expected frame and reports a difference.
- Stop the playback before *Transcribe* and while the result is imported – Resolve can hang when the page changes during playback.
- The media pool item points to the SRT in `resolve_temp\`; once that file has been deleted as the oldest of the last 10, the item is offline. The subtitles on the timeline belong to the project.
- `resolve/tools/probe_subtitles.lua` checks how a new Resolve version places an SRT – run it after a Resolve update.

---

## Settings

- **Main window:** language, mode, layout, audio filter and prompt. *Save as default* stores them in the INI.
- **Settings …** has seven tabs: Whisper, Speech detection, Subtitles, Audio & output, Languages, DaVinci Resolve and General. *Save* writes only the changed values to `whisper_tool.ini`, creates a `.bak` backup first and keeps all comments.
- `whisper_tool.ini` can also be edited by hand. Every value is commented; the template is `whisper_tool.example.ini`.

*Settings → Speech detection* also holds the two checks after the transcription: the gap check and the score check with its threshold. *Settings → DaVinci Resolve* decides which check tracks go along with a hand-over.

*Settings → Subtitles* is ordered by the questions the tool answers one after the other: **1 · Splitting** – which words go together into one subtitle, **2 · Pauses** – how long a segment stays on screen, **3 · Lyrics** – own rules for song lines.

![Settings – Subtitles](docs/images/settings-subtitles.png)

Changes take effect on the next start, without closing the window. The models are reloaded only when a model parameter changes.

---

## Interface language and translations

The interface, the log and the comparison report are available in **English, German and Turkish**.

![Settings – General](docs/images/settings-general.png)

- *Settings → General → Interface language*. The default “like Windows” uses the system language if there is a language file for it, otherwise English. After a change, the tool offers to restart the window.
- In the INI: `[System] ui_language = auto | en | de | tr`.

**Adding a language:**
1. Copy `lang/en.json` to `lang/<code>.json`, for example `lang/fr.json`.
2. Translate the values and set `"_meta" → "name"`. Keep placeholders like `{n}` and `{path}` unchanged.
3. Restart the tool. The new language appears in the list automatically.

Missing entries fall back to English, so a half-finished translation never breaks the tool. Pull requests with new languages are welcome.

---

## Command line

To run without a window, e.g. from a scheduled task, use the defaults from `[Menu]` in the INI:

```bash
venv\Scripts\python.exe whisper_tool.py --nogui file1.mp4 file2.mp3
```

To create a comparison report without the window:

```bash
venv\Scripts\python.exe wt_compare.py A.srt B.srt reference.srt --media video.mp4
```

```bash
venv\Scripts\python.exe wt_compare.py A.srt --ref reference.srt --lang de
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| After an update you still see the old interface (title “Whisper Tool”) | An old window is still open and took over the start. Close it – if it is not visible, end `pythonw.exe` in Task Manager – and start again |
| The tool does not start | A message box shows the reason. The same text is in `whisper_tool_error.log` |
| A line of the system check is red | It names the missing part and which feature is affected. See [Known ways to break the setup](#known-ways-to-break-the-setup) |
| “torch is a build without CUDA” | torch was installed from PyPI. Repeat step 4 of the installation |
| Text is missing in quiet passages | Look at the gap check. Then lower the *Onset* or try *silero* (*Settings → Speech detection*) |
| Wrong words in the subtitles | Switch on the score check (*Settings → Speech detection*) and listen to the places it lists. Fix recurring ones with a replacement or the prompt (*Settings → Languages*) |
| The score check marks many names that are right | Names and foreign words often have a low score. Lower the threshold, e.g. to 0.30 |
| Wrong language for the whole file | Set the language instead of *Automatic*; music or an intro at the start can mislead the detection |
| Words from the prompt appear in silence or music | Shorten the prompt or choose *none* for this file |
| The plugin is not listed under *Workspace → Scripts* in Resolve | Restart Resolve once; otherwise install the plugin again under *Settings → DaVinci Resolve* |
| The plugin says the workbench is not found | The workbench folder was moved – install the plugin again |
| The subtitles are at the wrong position in Resolve | Send the output of *Workspace → Console* (lines starting with `[WhisperX]`) |
| Drag & drop into the window does not work | *Settings → General → Drag & drop → Enable …*, then restart the window. Dropping onto the BAT always works |

---

## Project structure

| File | Purpose |
|---|---|
| `whisper_tool.py` | User interface (Tkinter), settings dialog, comparison dialog, console mode |
| `wt_core.py` | Audio loading, WhisperX, lyrics alignment, subtitle rules, gap check, score check, system check, INI writer |
| `wt_compare.py` | Word-by-word comparison and the HTML report (also usable on its own) |
| `wt_i18n.py` | Loads the language files |
| `wt_resolve.py` | DaVinci Resolve connection: job and result files, SRT shift, plugin installation |
| `resolve/WhisperX_Subtitles.lua` | The plugin for DaVinci Resolve (template; *Settings → DaVinci Resolve* installs it) |
| `resolve/tools/probe_subtitles.lua` | Test script: how Resolve places an SRT on a subtitle track |
| `wt_version.py` | Name and version number (shown in the title bar, log and report) |
| `lang/*.json` | All texts of the interface, log and report (en, de, tr) |
| `whisper_tool.example.ini` | Settings template; copied to `whisper_tool.ini` on the first start |
| `Whisper_Tool.bat` | Starter: double-click it or drop files onto it |
| `requirements.txt` | The tested package versions (install torch first!) |
| `constraints.txt` | Exact versions of all packages of the tested installation (`pip freeze`), used with `-c` in step 5 |
| `tests/` | Automatic tests (pytest). They need no GPU and no WhisperX and run on GitHub with every push. The tests of the Resolve plugin run against a fake Resolve and need the package `lupa`; without it they are skipped |
| `resolve_temp/` | Job folders of the plugin and the SRTs of the last 10 hand-overs (not part of the repository) |

---

## License and credits

[MIT License](LICENSE) © 2026 fardem

This tool builds on the work of others:
- [WhisperX](https://github.com/m-bain/whisperX)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and [CTranslate2](https://github.com/OpenNMT/CTranslate2)
- [OpenAI Whisper](https://github.com/openai/whisper)
- [pyannote.audio](https://github.com/pyannote/pyannote-audio)
- [PyTorch / torchaudio](https://pytorch.org/), including the MMS_FA forced-alignment pipeline
- [uroman](https://github.com/isi-nlp/uroman)
- [AutoSubs](https://github.com/tmoroney/auto-subs) (MIT) – its audio export from Resolve was the model for the render part of the plugin (own code)
- [FFmpeg](https://ffmpeg.org/)

Their licenses apply to those packages. The screenshots use made-up demo content.
