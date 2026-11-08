# Regeln für Claude in diesem Projekt

WhisperX Subtitle Workbench: Tkinter-Programm für Windows, Kern in Python, Plugin für
DaVinci Resolve in Lua. Die Dateien und ihre Aufgaben stehen in der README unter
„Aufbau des Projekts“.

## 1. Sprache

Im Chat: Deutsch, sachlich, kurz. Im Chat duzen wir uns.

Code-Kommentare, Docstrings, Commit-Messages, Pull Requests und das CHANGELOG sind
englisch. Kommentare und Commits kommen ohne Anrede aus.

Oberfläche und README sprechen den Leser an: Deutsch „du“, Englisch „you“, Türkisch
„siz“.

README gibt es in drei Sprachen. Hauptfassung ist `README.md` (Englisch), dazu
`README.de.md` und `README.tr.md`. Jede Änderung geht in alle drei Fassungen. Das
CHANGELOG gibt es nur auf Englisch.

- Ein Sachverhalt pro Satz.
- Normale Fachbegriffe: Repository, Branch, Commit, Test, Dialog, venv, INI, Timeline,
  Untertitelspur.
- Zahlen, Dateinamen und Zeilennummern statt Andeutungen.
- Bei einem Fehler: was ist passiert, wo, warum.
- Fettdruck nur für Ergebnisse und Zahlen, auf die es ankommt. Keine Wörter oder Sätze
  in Großbuchstaben.

Nicht:

- Metaphern für technische Dinge.
- Gehobene oder literarische Formulierungen, Erzählform, rhetorische Fragen.
- Slang.
- Erklärungen, nach denen niemand gefragt hat.

| statt | besser |
|---|---|
| „die Untertitel landen auf einer Untertitelspur“ | „die Untertitel kommen auf eine Untertitelspur“ |
| „wenn pyannote leise Sprache verschluckt“ | „wenn pyannote leise Sprache auslässt“ |
| „eine halb fertige Übersetzung legt das Tool nie lahm“ | „eine unvollständige Übersetzung führt nicht zu Fehlern“ |
| „Bekannte Wege, die Installation kaputt zu machen“ | „Häufige Installationsfehler“ |
| „Dieses Update würde die Kombination zerstören“ | „Dieses Update würde Pakete der getesteten Kombination ersetzen“ |

### Begriffe

Für Dinge der Oberfläche gilt der Name aus `lang/de.json`, nicht der aus dem Code.
Beispiel: „Prüfspuren“ und „Einstellungen → DaVinci Resolve → Analyse“, nicht
„extra tracks“ oder `_tab_resolve`. In englischen und türkischen Texten gilt der Name
aus `lang/en.json` und `lang/tr.json`.

- Das Programm heißt „die Workbench“ (englisch „the Workbench“, türkisch
  „Workbench“), nicht „das Tool“ oder „araç“.
- Der Haken einer Checkbox heißt „Häkchen“.
- Gängige englische Begriffe bleiben englisch, auch in der Oberfläche: Plugin, Update,
  Backup, Prompt, Beam, Batch, Timeline, Job, Drag & Drop, Score.

`tests/test_texts.py` meldet in den Sprachdateien und in der README diese Wörter:

| nicht | sondern |
|---|---|
| das Tool, dem Tool, ins Tool / the tool / araç, aracı | die Workbench / the Workbench / Workbench |
| verschluckt / swallows / yutar | auslassen, nicht erkennen / misses / atlar |
| kaputt machen | Fehler, ersetzen |
| Haken | Häkchen |

## 2. Was in einen Text gehört

Ein Text beschreibt die Stelle, an der er steht. Das gilt für Kommentare, die
INI-Vorlage, README-Abschnitte, Texte der Oberfläche, Tooltips und Meldungen im
Protokoll.

- Nur was der Leser an dieser Stelle braucht, um richtig zu handeln. Ein Satz, ohne den
  niemand einen Fehler macht, wird gestrichen.
- Was an anderer Stelle eingestellt oder getan wird, steht dort und nicht hier.
  Beispiel: Der Score-Check ist im Tooltip unter *Spracherkennung* erklärt; der Tooltip
  der Zeile unter *DaVinci Resolve → Analyse* sagt nur, dass es dieselbe Einstellung ist.
- Was die Erklärung eines Abschnitts (`set.sec_<name>_info`) sagt, wiederholt kein
  Tooltip darin.
- Keine Inhalte aus der README in Tooltips, Kommentare oder Modul-Docstrings kopieren.
  Höchstens ein Verweis auf den Abschnitt.
- Das Innenleben nur nennen, wenn der Leser es für eine Entscheidung braucht.
- Warnungen nur bei Datenverlust, bei Folgen für die Sicherheit oder wenn sonst die
  Installation nicht mehr läuft (torch aus PyPI statt vom PyTorch-Index).

### Konfigurationsdateien

`whisper_tool.example.ini`, `requirements.txt`, `constraints.txt`, `Whisper_Tool.bat`.
Je Einstellung höchstens vier Zeilen, meist reichen zwei. Sie nennen:

1. Name und Wirkung.
2. Was gilt, wenn der Wert 0, leer oder fehlend ist.
3. Wann und wie man ihn ändert, falls das nicht offensichtlich ist.

```ini
; countdown: seconds until such a job starts, independent of [Menu] timeout. 0 = press Start.
countdown = 10
```

In der INI steht ein Kommentar immer in einer eigenen Zeile vor dem Schlüssel; der
INI-Leser erlaubt keinen Kommentar hinter einem Wert. Tests ersetzen Zeilen wie
`prompt_mode   = ini` und `default_chars = 42` wörtlich: Wertzeilen nicht umformatieren.

## 3. Kommentare

Gilt für jeden Kommentar und Docstring in Python, Lua, INI, BAT und YAML, in jeder
Runde, bis der Betreiber die Regel ändert. Wird eine Zeile geändert, wird ihr Kommentar
mit angepasst oder gelöscht.

`tests/test_texts.py` prüft für jede Datei in `tests/comment_limits.json` die Zahl der
Kommentarzeilen und die Zahl der Blöcke mit mehr als drei Zeilen (INI: mehr als vier).
Nach dem Kürzen senkt `python tools/comments.py --write` die Grenzen auf den neuen
Stand. Angehoben werden sie nur mit Begründung im Commit. Eine neue Datei braucht
ebenfalls `--write`.

### Wann ein Kommentar steht

Nur, wenn der Code eine Frage offenlässt:

- warum diese Lösung und nicht die naheliegende
- eine Bedingung von außen: Verhalten der Resolve-API, Grenze von Resolves Lua (`io.open`
  nur mit ASCII-Pfaden), Verhalten von WhisperX, faster-whisper, ffmpeg oder Tkinter
- Einheit oder Herkunft einer Zahl (`READING_CPS = 17`, `SCORE_LETTERS = 3`)
- eine Reihenfolge, die nicht vertauscht werden darf
- eine Stelle, die zusammen mit einer anderen geändert werden muss, mit Datei- und
  Bezeichnername

Sagt ein besserer Name oder eine Konstante dasselbe, wird umbenannt statt kommentiert.

### Form

- Ein Satz, höchstens drei Zeilen. Gilt auch für Docstrings. Nie mehr Kommentar als
  Code darunter.
- Präsens, beschreibt den jetzigen Stand.
- Stichwort- und Infinitivstil sind erlaubt: „Remove the list of an earlier run.“
- Keine Betonung durch Großbuchstaben. Groß bleiben nur Abkürzungen (INI, SRT, VAD)
  und Namen von Konstanten (`INCLUDE_START_TC`).
- Abschnittsmarken in langen Dateien stehen in einer Zeile, in normaler Schreibung:
  `# ─── Gap check ───…` in Python, `-- ─── Render ───…` in Lua.
- Keine Metaphern, keine Sätze mit „Whoever …“, keine Anrede.
- Länger darf nur eine Tabelle gemessener Werte sein: eine Zeile, was gemessen wurde,
  dann die Werte, kein Fließtext darum. Beispiel: die Ergebnisse in
  `resolve/tools/probe_subtitles.lua`. Die Lizenzangabe zu AutoSubs im Kopf von
  `resolve/WhisperX_Subtitles.lua` bleibt.

### Was nicht in einen Kommentar gehört

Das steht in Git oder im CHANGELOG:

- was vorher dort stand, was „korrigiert“ wurde, „as before“
- die Abwägung
- eigene Versionsnummern
- Bedienung des Programms (steht in der README)

Außerdem nicht: die Wiederholung der Zeile darunter und die Wiedergabe dessen, was eine
andere Datei tut. Dort genügen Datei- und Bezeichnername.

In Tests beschreibt ein Kommentar alte Eingaben über ihren Inhalt, nicht über die
Version: `# INI without [Score]`, nicht `# INI of 0.6.3`.

### Beispiele aus dem Repository

| statt | besser |
|---|---|
| `whisper_tool.py`: „Only to a window of the SAME version – a different (e.g. older) version that is still open would otherwise silently take over, and the new start would be invisible.“ | „Only a window of the same version takes them: another version would take them without showing the new start.“ |
| `wt_core.py`: Docstring „Smart split with: - filler filter - replacements … - abbreviations and ordinals …“ (8 Zeilen) | „Mode "Sentences": fillers, replacements, abbreviations, orphan, dangling word and pause rule, line balancing.“ |
| `wt_resolve.py`: `LEGACY_DIR … # the same in 0.5.0` | `# the same in older versions` |
| `wt_core.py`: „track_gaps / track_fast replace extra_tracks (0.6.x) – a missing key takes its value“ | „A missing track_gaps / track_fast takes the value of the old key extra_tracks.“ |
| `# ─── USER INTERFACE ───` | `# ─── User interface ───` |

## 4. Oberfläche und Sprachdateien

- Jeder sichtbare Text steht in `lang/en.json`, `lang/de.json` und `lang/tr.json`, mit
  denselben Schlüsseln und Platzhaltern (`tests/test_i18n.py`). Keine Texte im Code.
- Tooltip: `tip.<key>`, Hinweis rechts neben einer Zeile: `set.<key>_hint`, Abschnitt im
  Einstellungsdialog: `set.sec_<name>` und `set.sec_<name>_info`.
- Die Texte unter `plugin.*` schreibt die Installation fest in das Plugin. Ändert sich
  einer, muss das Plugin neu installiert werden; das steht dann im Update-Hinweis des
  CHANGELOG.
- Die Spurnamen in Resolve (`plugin.track_*`) stehen wörtlich in den Zeilen unter
  *Analyse* (`tests/test_settings_switch.py`).
- Ein Reiter des Einstellungsdialogs ist ein einziges Grid, damit die Zahlenfelder
  untereinander stehen. Zeilen, die nicht dazu passen, bekommen ein eigenes Frame
  (Beispiel: Plugin-Status in `_tab_resolve`).
- Die Bildschirmfotos in `docs/images/` zeigen die englische Oberfläche der Workbench,
  aufgenommen unter Linux mit Xvfb (100 dpi, Hauptfenster 1042 × 791). Ändert sich ein Dialog
  oder ein Text darin, nimmt Claude das Bild neu auf und nennt es in der Antwort. Bilder aus
  DaVinci Resolve nimmt der Betreiber auf.

## 5. Ablauf

- Entwicklung auf dem Branch, der in der Aufgabe genannt ist.
- Vor jedem Push läuft `python -m pytest -q` vollständig. Das Ergebnis wird genannt. Die
  Tests des Plugins brauchen `lupa`; ohne das Paket werden sie übersprungen, das wird
  dann ebenfalls genannt.
- Commit: erste Zeile sagt, was geändert wurde, höchstens 72 Zeichen, englisch. Nach
  einer Leerzeile der Grund, wenn er nicht offensichtlich ist.
- Pull Request nur, wenn er verlangt wurde.

### Fragen an den Betreiber

- Rückfragen, bei denen der Betreiber zwischen Wegen wählt, stehen in Fragetafeln
  (`AskUserQuestion`): höchstens vier Fragen je Tafel, die empfohlene Antwort zuerst und
  mit „(Empfohlen)“, zu jeder Antwort eine Zeile, was sie bewirkt.
- Die Fragen kommen vor der ersten Zeile Code. Gebaut wird erst mit allen Antworten.
- Wird eine Tafel weggeklickt, wird nicht gebaut. Die Tafel kommt noch einmal, wenn der
  Betreiber es verlangt.
- Kommt beim Bauen eine neue Frage auf, wird sie ebenso als Fragetafel gestellt.
  Antwortet niemand, gilt der einfachere Weg, der kein Verhalten ändert.

## 6. Versionen und Verträglichkeit

- Versionsschema MAJOR.MINOR.PATCH (semver.org).
- Die eigene Versionsnummer steht nur in `wt_version.py`, im WX-CONFIG-Block der
  Vorlage `resolve/WhisperX_Subtitles.lua`, im Kopf der drei READMEs
  (`**Version x.y.z**`) und im CHANGELOG. Bei einem Versionswechsel werden alle fünf
  Stellen geändert; `tests/test_texts.py` prüft den README-Kopf.
- Nicht in Kommentaren, Texten der Oberfläche und des Protokolls, der INI-Vorlage und im
  Text der README. Verhalten wird ohne Nummer beschrieben: „aus, wenn die INI keinen
  Abschnitt `[Score]` hat“, nicht „in einer INI von vor 0.7.0 aus“.
- Versionen anderer Software bleiben: Python 3.10, torch 2.8.0, whisperx 3.8.x,
  DaVinci Resolve Studio 21.1.2.
- Die Workbench ist veröffentlicht. Eine vorhandene `whisper_tool.ini` muss nach einem
  Update weiter funktionieren: Ein fehlender Schlüssel bekommt seinen Vorgabewert oder
  den Wert des alten Schlüssels (Beispiel: `extra_tracks` → `track_gaps`, `track_fast`).
  `tests/test_config.py` prüft das.
- Jede Version bekommt im CHANGELOG einen Update-Hinweis („Updating from x.y.z“): was
  der Nutzer tun muss (Plugin neu installieren, INI-Schlüssel, Python-Pakete).
- Eine neue Version zeigt das installierte Plugin unter *Einstellungen → DaVinci
  Resolve* als veraltet an.

## 7. Zahlen

Gemessen wird am fertigen Stand, nicht an einem Zwischenstand. Wird eine
veröffentlichte Zahl korrigiert, steht daneben, warum sie sich geändert hat.
