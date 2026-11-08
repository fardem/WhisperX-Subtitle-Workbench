#!/usr/bin/env python3
"""
WhisperX Subtitle Workbench: main window, settings dialog and the command line (--nogui).
Texts of the interface in lang/<code>.json, settings in whisper_tool.ini.
"""

from __future__ import annotations

import os
import sys
import warnings

# Silence library warnings before any import
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
for _cat in (UserWarning, FutureWarning, DeprecationWarning):
    warnings.filterwarnings("ignore", category=_cat)

import argparse
import queue
import re
import shutil
import socket
import subprocess
import threading
import time
import traceback
from collections import deque
from dataclasses import replace
from pathlib import Path

HERE        = Path(__file__).resolve().parent
DEFAULT_INI = HERE / "whisper_tool.ini"
EXAMPLE_INI = HERE / "whisper_tool.example.ini"
ERROR_LOG   = HERE / "whisper_tool_error.log"
sys.path.insert(0, str(HERE))      # wt_core/wt_i18n/wt_compare/wt_version live next to this script
try:
    from wt_version import APP_NAME, VERSION
except Exception:
    APP_NAME, VERSION = "WhisperX Subtitle Workbench", "?"


def _fatal(msg: str):
    """Make start errors visible – even without a console (pythonw) and without Tkinter."""
    try:
        ERROR_LOG.write_text(msg, encoding="utf-8")
    except OSError:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, APP_NAME, 0x10)
            return
        except Exception:
            pass
    print(msg, file=sys.__stderr__ or sys.stdout)


try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, font as tkfont
except ImportError:
    tk = None
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
except Exception:
    TkinterDnD = DND_FILES = None



def _ini_arg() -> Path:
    """--ini from the command line – the language must be known before anything else."""
    for i, a in enumerate(sys.argv):
        if a == "--ini" and i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1])
        if a.startswith("--ini="):
            return Path(a[6:])
    return DEFAULT_INI


try:
    import wt_i18n as i18n
    from wt_i18n import T, Tn
except Exception as _e:
    _fatal(f"{APP_NAME}: wt_i18n.py is missing or broken in {HERE}\n\n{_e}")
    sys.exit(1)
if not i18n.available():
    _fatal(f"{APP_NAME}: the folder 'lang' with the language files is missing in {HERE}")
    sys.exit(1)
_ini = _ini_arg()
i18n.load(i18n.ini_language(_ini if _ini.exists() else EXAMPLE_INI))

try:
    import wt_core as core
except Exception as _e:
    if not (HERE / "wt_core.py").exists():
        _why = T("app.err_core_missing", dir=HERE)
    elif isinstance(_e, ModuleNotFoundError):
        _why = T("app.err_package_missing", name=_e.name, python=sys.executable)
    else:
        _why = f"{_e.__class__.__name__}: {_e}"
    _fatal(T("app.err_start", why=_why, log=ERROR_LOG))
    sys.exit(1)
try:
    import wt_compare
except Exception:
    wt_compare = None      # without wt_compare.py there is no text comparison
try:
    import wt_resolve
except Exception:
    wt_resolve = None      # without wt_resolve.py there is no DaVinci Resolve connection


def lang_choices() -> list:
    return [("auto", T("speechlang.auto"))] + [(c, T(f"speechlang.{c}")) for c in core.LANGS]


def mode_choices() -> list:
    return [("sentence", T("mode.sentence")), ("word", T("mode.word")), ("raw", T("mode.raw_segments"))]


def kind_label(kind: str, job=None) -> str:
    return T("resolve.kind") if job is not None else T(f"kind.{kind}")


def ensure_ini(path: Path):
    """First start: create whisper_tool.ini from the template (so updates never overwrite it)."""
    if not path.exists() and path.resolve() == DEFAULT_INI.resolve() and EXAMPLE_INI.exists():
        try:
            shutil.copyfile(EXAMPLE_INI, path)
        except OSError:
            pass

# Further starts hand their files to the open window (local only). Only a window of the
# same version takes them: another version would take them without showing the new start.
IPC_ADDR  = ("127.0.0.1", 47653)
IPC_HELLO = f"WHISPERTOOL/2 {VERSION}\n".encode()
IPC_OK    = b"WHISPERTOOL-OK"
IPC_OTHER = b"WHISPERTOOL-OTHER "


def fmt_dur(sec: float) -> str:
    if sec < 60:
        return i18n.secs(sec)
    m, s = divmod(int(round(sec)), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else T("main.minutes", t=f"{m}:{s:02}")


def fmt_clock(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m:02}:{s:02}"


# Live line from WhisperX (verbose):  Transcript: [12.34 --> 17.8] Text
LIVE_RE = re.compile(r"Transcript: \[([\d.]+) --> ([\d.]+)\] ?(.*)", re.S)


def label_of(choices, key):
    return next((lbl for k, lbl in choices if k == key), choices[0][1])


def key_of(choices, label):
    return next((k for k, lbl in choices if lbl == label), choices[0][0])


class Tooltip:
    """Explanation shown when the mouse rests on a control."""
    DELAY = 450

    def __init__(self, widget, text: str, scale: float = 1.0):
        self.w, self.text, self.scale = widget, text, scale
        self.tip = self.job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._hide()
        self.job = self.w.after(self.DELAY, self._show)

    def _show(self):
        self.job = None
        if self.tip or not self.w.winfo_exists():
            return
        # build and position it invisibly first – otherwise the window briefly appears
        # top left, may end up under the mouse and immediately trigger <Leave>
        self.tip = tw = tk.Toplevel(self.w)
        tw.withdraw()
        tw.wm_overrideredirect(True)
        tw.attributes("-topmost", True)
        tk.Label(tw, text=self.text, justify="left", wraplength=int(460 * self.scale),
                 background="#fffbe6", foreground="#202020", relief="solid", borderwidth=1,
                 padx=9, pady=7).pack()
        tw.update_idletasks()
        w, h = tw.winfo_reqwidth(), tw.winfo_reqheight()
        x, y = self.w.winfo_pointerx() + 16, self.w.winfo_pointery() + 20
        x = min(x, tw.winfo_screenwidth() - w - 8)
        if y + h > tw.winfo_screenheight() - 40:
            y = self.w.winfo_pointery() - h - 12
        tw.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        tw.deiconify()
        self.job = self.w.after(30000, self._hide)      # never stay forever

    def _hide(self, _e=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


def tip(widgets, key: str, scale: float = 1.0):
    """Attach a tooltip (lang/<code>.json → "tip") to one or more widgets."""
    text = T(f"tip.{key}", suffix=T("core.gap_suffix"), score_suffix=T("core.score_suffix")) \
        if i18n.has(f"tip.{key}") else ""
    if text:
        for w in widgets if isinstance(widgets, (list, tuple)) else (widgets,):
            Tooltip(w, text, scale)


def ask_buttons(parent, title: str, text: str, buttons: list[tuple[str, str]]) -> str | None:
    """Modal question with own buttons (messagebox has fixed ones) → key of the button, None = window closed."""
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.transient(parent)
    dlg.resizable(False, False)
    dlg.geometry(f"+{parent.winfo_rootx() + 80}+{parent.winfo_rooty() + 80}")
    ttk.Label(dlg, text=text, justify="left", wraplength=int(480 * parent.scale), padding=16).pack(fill="x")
    bar = ttk.Frame(dlg, padding=(16, 0, 16, 16))
    bar.pack(fill="x")
    answer = []
    for i, (key, label) in reversed(list(enumerate(buttons))):
        b = ttk.Button(bar, text=label, style="Accent.TButton" if i == 0 else "TButton",
                       command=lambda k=key: (answer.append(k), dlg.destroy()))
        b.pack(side="right", padx=(8, 0))
        if i == 0:
            b.focus_set()
    dlg.bind("<Escape>", lambda e: dlg.destroy())
    dlg.grab_set()
    dlg.wait_window()
    return answer[0] if answer else None


def plugin_state_text(status: str, info: dict | None) -> str:
    if status == "missing":
        return T("set.resolve_not_installed")
    if status == "other":
        return T("set.resolve_other", path=info["workbench"])
    if status == "outdated":
        return T("set.resolve_outdated", version=info.get("version") or "?")
    return T("set.resolve_installed", version=info["version"])


def plugin_start_action(status: str, skip: str, can_ask: bool) -> str:
    if status in ("missing", "current"):
        return ""
    return "ask" if status == "outdated" and can_ask and skip != VERSION else "log"


class QueueWriter:
    """Redirects print()/logging of the libraries into the log window."""
    encoding = "utf-8"

    def __init__(self, q, orig):
        self.q, self.orig = q, orig

    def write(self, s):
        if s:
            self.q.put(("log", s))
            if self.orig is not None:
                try:
                    self.orig.write(s)
                except Exception:
                    pass
        return len(s)

    def flush(self):
        if self.orig is not None:
            try:
                self.orig.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def send_to_running(files) -> tuple[str, str]:
    """
    Is a window already open? → ("ok", "") files were handed over to it, ("none", "") no window,
    ("other", version) a window of another version is open ("" if its reply names no version).
    """
    try:
        with socket.create_connection(IPC_ADDR, timeout=1.0) as s:
            s.sendall(IPC_HELLO + "\n".join(str(Path(f).resolve()) for f in files).encode("utf-8"))
            s.shutdown(socket.SHUT_WR)
            s.settimeout(3.0)
            reply = s.recv(128)
    except OSError:
        return "none", ""
    if reply == IPC_OK:
        return "ok", ""
    if reply.startswith(IPC_OTHER):
        return "other", reply[len(IPC_OTHER):].decode("utf-8", "replace").strip()
    return "other", ""


# ─── User interface ───────────────────────────────────────────────────────────

BaseTk = TkinterDnD.Tk if TkinterDnD else (tk.Tk if tk else object)


class App(BaseTk):
    POLL_MS = 100

    def __init__(self, ini_path: Path, files: list[str]):
        super().__init__()
        self.ini_path  = Path(ini_path)
        self.cfg       = core.Config(self.ini_path)
        self.q         = queue.Queue()
        sys.stdout     = QueueWriter(self.q, sys.__stdout__)
        sys.stderr     = QueueWriter(self.q, sys.__stderr__)
        self.engine    = core.Engine(self.cfg,
                                     log=lambda m: self.q.put(("log", m + "\n")),
                                     progress=lambda stage, pct: self.q.put(("progress", stage, pct)))
        self.items: dict[str, dict] = {}
        self.extra     = queue.Queue()      # files added during a run
        self.worker    = None
        self.countdown = None
        self.countdown_resolve = False      # the running countdown is the one for DaVinci Resolve jobs
        self.current   = None

        self.title(f"{APP_NAME} {VERSION}")
        self.scale = max(1.0, self.winfo_fpixels("1i") / 96)
        self.geometry(f"{int(1000 * self.scale)}x{int(760 * self.scale)}")
        self.minsize(int(860 * self.scale), int(620 * self.scale))
        self._build_ui()

        self._append_log(i18n.line("info", "main.version", app=APP_NAME, version=VERSION, folder=HERE) + "\n")
        for w in self.cfg.warnings + [f"lang/{e}" for e in i18n.errors]:
            self._append_log(f"{i18n.tag('warn')}{w}\n")
        self._append_log(i18n.line("info", "main.settings_file", path=self.ini_path) + "\n")
        new = self.add_files(files)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(self.POLL_MS, self._poll)
        self._start_ipc()
        self.after(300, self.run_check)          # system check on every start
        if wt_resolve:                           # left-over job folders of the DaVinci Resolve plugin
            threading.Thread(target=self._cleanup_resolve_jobs, daemon=True).start()

        if self.items:
            jobs = [i for i in new if self.items[i].get("job")]
            if jobs:
                self._resolve_arrived(jobs)
            elif self.cfg.menu_skip:
                self.after(400, self.start)
            elif self.cfg.menu_timeout > 0:
                self._start_countdown(self.cfg.menu_timeout)

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        cfg   = self.cfg
        style = ttk.Style(self)
        if sys.platform != "win32" and "clam" in style.theme_names():
            style.theme_use("clam")
        base = tkfont.nametofont("TkDefaultFont")
        bold = base.copy()
        bold.configure(weight="bold")
        style.configure("Accent.TButton", font=bold, padding=(16, 6))
        style.configure("Treeview", rowheight=int(24 * self.scale))
        mono = "Consolas" if "Consolas" in tkfont.families() else "TkFixedFont"

        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=2)
        root.rowconfigure(4, weight=3)

        # ── Files ──────────────────────────────────────────────────────────────
        S = self.scale
        l_files = ttk.Label(root, text=T("main.files") + "  ⓘ")
        lf = ttk.LabelFrame(root, labelwidget=l_files, padding=8)
        tip(l_files, "files", S)
        lf.grid(row=0, column=0, sticky="nsew")
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(lf, columns=("kind", "status"), show="tree headings", selectmode="extended")
        self.tree.heading("#0", text=T("main.col_file"), anchor="w")
        self.tree.heading("kind", text=T("main.col_kind"), anchor="w")
        self.tree.heading("status", text=T("main.col_status"), anchor="w")
        self.tree.column("#0", width=int(470 * self.scale), stretch=True)
        self.tree.column("kind", width=int(100 * self.scale), stretch=False)
        self.tree.column("status", width=int(250 * self.scale), stretch=False)
        self.tree.tag_configure("running", foreground="#1a5fb4")
        self.tree.tag_configure("done", foreground="#26a269")
        self.tree.tag_configure("error", foreground="#c01c28")
        self.tree.tag_configure("cancel", foreground="#77767b")
        self.tree.tag_configure("gaps", foreground="#b35c00")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", self._reveal)
        self.tree.bind("<Delete>", lambda e: self._remove_selected())
        if DND_FILES:          # optional: package tkinterdnd2 – drag files directly into the window
            for w in (self, self.tree):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)

        fb = ttk.Frame(lf)
        fb.grid(row=0, column=2, sticky="n", padx=(8, 0))
        b_add = ttk.Button(fb, text=T("main.add"), command=self._ask_files)
        b_add.pack(fill="x")
        self.b_remove = ttk.Button(fb, text=T("main.remove"), command=self._remove_selected)
        self.b_remove.pack(fill="x", pady=4)
        self.b_clear = ttk.Button(fb, text=T("main.clear"), command=self._clear)
        self.b_clear.pack(fill="x")
        b_folder = ttk.Button(fb, text=T("main.open_folder"), command=self._open_folder)
        b_folder.pack(fill="x", pady=(18, 0))
        b_cmp = ttk.Button(fb, text=T("main.compare"), command=self._compare_dialog)
        b_cmp.pack(fill="x", pady=4)
        if wt_compare is None:
            b_cmp.state(["disabled"])
        for w, key in ((b_add, "add"), (self.b_remove, "remove"), (self.b_clear, "clear"), (b_folder, "folder"),
                       (b_cmp, "compare")):
            tip(w, key, S)

        hint = (T("main.hint_drop") if DND_FILES else T("main.hint_bat")) + "  ·  " + T("main.hint_dblclick")
        l_hint = ttk.Label(lf, text=hint, foreground="#77767b")
        l_hint.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        tip(l_hint, "files", S)

        # ── Options ────────────────────────────────────────────────────────────
        of = ttk.LabelFrame(root, text=T("main.options"), padding=8)
        of.grid(row=1, column=0, sticky="ew", pady=8)
        for c in (1, 3, 5, 7):
            of.columnconfigure(c, weight=1)
        px = dict(padx=(0, 6), pady=3)

        self.lang_choices, self.mode_choices = lang_choices(), mode_choices()
        self.v_lang   = tk.StringVar(value=label_of(self.lang_choices, cfg.default_language))
        self.v_mode   = tk.StringVar(value=label_of(self.mode_choices, cfg.default_mode))
        self.v_chars  = tk.StringVar(value=str(cfg.default_chars))
        self.v_lines  = tk.StringVar(value=str(cfg.default_lines))
        self.v_filter = tk.BooleanVar(value=cfg.default_filter)
        self.v_json   = tk.BooleanVar(value=cfg.keep_json)
        self.v_hold   = tk.StringVar(value=f"{cfg.line_hold:g}")
        self.v_pmode  = tk.StringVar(value=cfg.prompt_mode)
        self.v_ptext  = tk.StringVar()
        self._custom_text = cfg.custom_prompt

        l_lang = ttk.Label(of, text=T("main.language"))
        l_lang.grid(row=0, column=0, sticky="w", **px)
        cb_lang = ttk.Combobox(of, textvariable=self.v_lang, state="readonly", width=14,
                               values=[l for _, l in self.lang_choices])
        cb_lang.grid(row=0, column=1, sticky="ew", **px)
        l_mode = ttk.Label(of, text=T("main.mode"))
        l_mode.grid(row=0, column=2, sticky="w", padx=(12, 6))
        cb_mode = ttk.Combobox(of, textvariable=self.v_mode, state="readonly", width=22,
                               values=[l for _, l in self.mode_choices])
        cb_mode.grid(row=0, column=3, sticky="ew", **px)
        l_chars = ttk.Label(of, text=T("main.chars"))
        l_chars.grid(row=0, column=4, sticky="w", padx=(12, 6))
        self.sp_chars = ttk.Spinbox(of, from_=core.CHARS_RANGE[0], to=core.CHARS_RANGE[1], textvariable=self.v_chars,
                                    width=6)
        self.sp_chars.grid(row=0, column=5, sticky="w", **px)
        l_lines = ttk.Label(of, text=T("main.lines"))
        l_lines.grid(row=0, column=6, sticky="w", padx=(12, 6))
        self.sp_lines = ttk.Spinbox(of, from_=core.LINES_RANGE[0], to=core.LINES_RANGE[1], textvariable=self.v_lines,
                                    width=4)
        self.sp_lines.grid(row=0, column=7, sticky="w", **px)

        c_filter = ttk.Checkbutton(of, text=T("main.filter"), variable=self.v_filter)
        c_filter.grid(row=1, column=0, columnspan=2, sticky="w", **px)
        c_json = ttk.Checkbutton(of, text=T("main.keep_json"), variable=self.v_json)
        c_json.grid(row=1, column=2, columnspan=2, sticky="w", padx=(12, 6))
        l_hold = ttk.Label(of, text=T("main.hold"))
        l_hold.grid(row=1, column=4, columnspan=2, sticky="w", padx=(12, 6))
        sp_hold = ttk.Spinbox(of, from_=0, to=10, increment=0.5, textvariable=self.v_hold, width=4)
        sp_hold.grid(row=1, column=6, sticky="w", **px)
        b_save = ttk.Button(of, text=T("main.save_defaults"), command=self._save_defaults)
        b_save.grid(row=1, column=7, sticky="e", **px)

        pf = ttk.Frame(of)
        pf.grid(row=2, column=0, columnspan=8, sticky="ew", pady=(6, 0))
        pf.columnconfigure(4, weight=1)
        l_prompt = ttk.Label(pf, text=T("main.prompt"))
        l_prompt.grid(row=0, column=0, sticky="w", padx=(0, 10))
        for col, (val, txt) in enumerate((("ini", T("main.pmode_ini")), ("custom", T("main.pmode_custom")), ("none", T("main.pmode_none"))), 1):
            rb = ttk.Radiobutton(pf, text=txt, value=val, variable=self.v_pmode, command=self._update_prompt_field)
            rb.grid(row=0, column=col, sticky="w", padx=(0, 10))
            tip(rb, f"pmode_{val}", S)
        self.e_prompt = ttk.Entry(pf, textvariable=self.v_ptext)
        self.e_prompt.grid(row=0, column=4, sticky="ew")

        for ws, key in (((l_lang, cb_lang), "lang"), ((l_mode, cb_mode), "mode"),
                        ((l_chars, self.sp_chars), "chars"), ((l_lines, self.sp_lines), "lines"),
                        (c_filter, "filter"), (c_json, "json"), ((l_hold, sp_hold), "hold"),
                        (b_save, "save_defaults"), ((l_prompt, self.e_prompt), "prompt_field")):
            tip(ws, key, S)

        cb_lang.bind("<<ComboboxSelected>>", lambda e: self._update_prompt_field())
        cb_mode.bind("<<ComboboxSelected>>", lambda e: self._update_mode_fields())
        self._update_prompt_field()
        self._update_mode_fields()

        # ── Start / status ─────────────────────────────────────────────────────
        bf = ttk.Frame(root)
        bf.grid(row=2, column=0, sticky="ew")
        bf.columnconfigure(3, weight=1)
        self.b_start = ttk.Button(bf, text=T("main.start"), style="Accent.TButton", command=self.start)
        self.b_start.grid(row=0, column=0)
        self.b_cancel = ttk.Button(bf, text=T("main.cancel"), command=self.cancel, state="disabled")
        self.b_cancel.grid(row=0, column=1, padx=8)
        # only visible with a job from DaVinci Resolve in the list, blue and active once it is
        # transcribed (a tk button: the native Windows theme ignores colors of ttk buttons)
        self.b_resolve = tk.Button(bf, text=T("resolve.hand_over"), command=self._hand_over, state="disabled",
                                   font=bold, relief="flat", bd=0, padx=14, pady=4, cursor="hand2",
                                   fg="white", activeforeground="white", disabledforeground="#8c8c8c")
        self._resolve_bg = (self.b_resolve.cget("background"), "#1a66d2", "#1557b0")   # off, on, pressed
        self.b_resolve.grid(row=0, column=2, padx=(0, 8), sticky="ns")
        self.b_resolve.grid_remove()
        self.l_status = ttk.Label(bf, text=T("main.ready"))
        self.l_status.grid(row=0, column=3, sticky="w", padx=8)
        b_set = ttk.Button(bf, text=T("main.settings"), command=lambda: SettingsDialog(self))
        b_set.grid(row=0, column=4)
        for w, key in ((self.b_start, "start"), (self.b_cancel, "cancel"), (self.b_resolve, "resolve_hand_over"),
                       (b_set, "settings")):
            tip(w, key, S)

        self.pb = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.pb.grid(row=3, column=0, sticky="ew", pady=(8, 8))

        # ── Log ────────────────────────────────────────────────────────────────
        l_log = ttk.Label(root, text=T("main.log") + "  ⓘ")
        lg = ttk.LabelFrame(root, labelwidget=l_log, padding=4)
        tip(l_log, "log", S)
        lg.grid(row=4, column=0, sticky="nsew")
        lg.columnconfigure(0, weight=1)
        lg.rowconfigure(0, weight=1)
        self.log_text = tk.Text(lg, height=10, wrap="word", font=(mono, 9), state="disabled",
                                background="#1e1e1e", foreground="#d4d4d4", insertbackground="#d4d4d4",
                                relief="flat", padx=6, pady=4)
        lsb = ttk.Scrollbar(lg, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lsb.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        lsb.grid(row=0, column=1, sticky="ns")
        self.log_text.tag_configure("warn", foreground="#e5c07b")
        self.log_text.tag_configure("error", foreground="#ef6b73")
        self.log_text.tag_configure("ok", foreground="#98c379")
        self.log_text.tag_configure("head", foreground="#61afef", font=(mono, 9, "bold"))
        self.log_text.tag_configure("time", foreground="#7f848e")
        self.log_text.tag_configure("live", foreground="#ffffff")

    def _update_prompt_field(self):
        mode = self.v_pmode.get()
        lang = key_of(self.lang_choices, self.v_lang.get())
        prev = getattr(self, "_pmode_prev", None)
        if prev == "custom" and mode != "custom":
            self._custom_text = self.v_ptext.get()          # remember the custom text
        self._pmode_prev = mode
        if mode == "custom":
            if prev != "custom":
                # restore the remembered text – otherwise the INI prompt as a template
                self.v_ptext.set(getattr(self, "_custom_text", "")
                                 or self.cfg.rules(lang if lang != "auto" else "de").prompt)
            self.e_prompt.state(["!readonly", "!disabled"])
        elif mode == "ini":
            self.v_ptext.set(T("main.prompt_auto") if lang == "auto"
                             else (self.cfg.rules(lang).prompt or T("main.prompt_empty")))
            self.e_prompt.state(["readonly", "!disabled"])
        else:
            self.v_ptext.set("")
            self.e_prompt.state(["disabled"])

    def _update_mode_fields(self):
        state = "!disabled" if key_of(self.mode_choices, self.v_mode.get()) == "sentence" else "disabled"
        self.sp_chars.state([state])
        self.sp_lines.state([state])

    # ── File list ──────────────────────────────────────────────────────────────

    def add_files(self, paths) -> list[str]:
        known = {it["key"] for it in self.items.values()}
        expanded, new = [], []
        for p in map(Path, paths):
            if p.is_dir():
                expanded += sorted(c for c in p.iterdir() if c.suffix.lower() in core.MEDIA_EXT)
            else:
                expanded.append(p)
        for p in expanded:
            if not p.exists():
                self._append_log(i18n.line("warn", "main.not_found", path=p) + "\n")
                continue
            try:
                kind, src, out = core.resolve_input(p, self.cfg)
            except Exception as e:
                self._append_log(f"{i18n.tag('warn')}{e}\n")
                continue
            key = str(src.resolve()).lower()
            if key in known:
                continue
            known.add(key)
            # Resolve mode: only for a file with the job file of the DaVinci Resolve plugin next to it
            job = wt_resolve.find_job(src) if wt_resolve and kind == "speech" else None
            text = T("resolve.item", timeline=job.timeline or src.name) if job else src.name
            iid = self.tree.insert("", "end", text=text, values=(kind_label(kind, job), T("status.wait")))
            self.items[iid] = {"path": p, "key": key, "kind": kind, "out": out, "state": "wait", "job": job}
            new.append(iid)
            if job:
                rng = T("resolve.range_all" if job.whole else "resolve.range_inout", dur=fmt_clock(job.duration()))
                self._append_log(i18n.line("info", "resolve.log_job", timeline=job.timeline, range=rng,
                                           fps=job.fps_text or f"{job.fps:g}") + "\n")
                self._log_resolve_options(job)
                self._resolve_progress(iid, "queued")
        if new and self._running():
            for iid in new:
                self.extra.put(iid)
            self._append_log(i18n.tag("info") + Tn("main.appended", len(new)) + "\n")
        self._update_resolve_ui()
        return new

    def _on_drop(self, e):
        """Files/folders dropped into the window (only with tkinterdnd2)."""
        self.add_files(self.tk.splitlist(e.data))
        return e.action

    # ── System check ───────────────────────────────────────────────────────────

    def run_check(self, manual=False, parent=None):
        """Checks packages, ffmpeg and graphics card in the background (loading torch takes a few seconds)."""
        def work():
            try:
                res = core.check_environment(core.Config(self.ini_path))
            except Exception as e:
                res = [("error", T("check.failed", err=e))]
            self.q.put(("check", res, manual, parent))
        threading.Thread(target=work, daemon=True).start()

    def _show_check(self, res, manual, parent):
        icon = {"ok": "✓", "info": "·", "warn": "!", "error": "✗"}
        self._append_log(i18n.line("check", "check.title") + "\n")
        for level, text in res:
            self._append_log(f"{i18n.tag('check')}{icon[level]} {text}\n")
        errors = [t for lvl, t in res if lvl == "error"]
        warns  = [t for lvl, t in res if lvl == "warn"]
        if errors:
            self._stop_countdown(silent=True)      # show the problem first, do not start automatically
            self.l_status.config(text=Tn("check.status_problems", len(errors)))
        elif manual:
            self.l_status.config(text=T("check.status_hints" if warns else "check.status_ok"))
        if errors or manual:
            text = "\n\n".join(f"{icon[lvl]}  {t}" for lvl, t in res if lvl in (("error", "warn") if errors else icon))
            head = T("check.head_errors" if errors else "check.head_ok") + "\n\n"
            (messagebox.showerror if errors else messagebox.showinfo)(
                T("check.title"), head + text, parent=parent if parent and parent.winfo_exists() else self)
        if not manual and wt_resolve:       # after the message of the check, so two dialogs do not overlap
            self._plugin_on_start()

    # ── Add drag & drop later ──────────────────────────────────────────────────

    DND_REQ = "tkinterdnd2==0.7.0"

    def install_dnd(self, parent):
        if not messagebox.askyesno(T("dnd.title"), T("dnd.confirm", pkg=self.DND_REQ, prefix=sys.prefix),
                                   parent=parent):
            return
        py = Path(sys.executable)
        if py.name.lower() == "pythonw.exe" and py.with_name("python.exe").exists():
            py = py.with_name("python.exe")

        def work():
            self.q.put(("log", f"{i18n.tag('dnd')}pip install --no-deps {self.DND_REQ} …\n"))
            try:
                r = subprocess.run([str(py), "-m", "pip", "install", "--no-deps", "--disable-pip-version-check",
                                    self.DND_REQ], capture_output=True, text=True, timeout=600,
                                   creationflags=core.NO_WINDOW)
                out = ((r.stdout or "") + (r.stderr or "")).strip()
                self.q.put(("log", "\n".join(f"{i18n.tag('dnd')}{l}" for l in out.splitlines()[-8:]) + "\n"))
                ok = r.returncode == 0
                msg = T("dnd.done") if ok else T("dnd.failed") + "\n\n" + out[-800:]
            except (OSError, subprocess.TimeoutExpired) as e:
                ok, msg = False, T("dnd.pip_failed", err=e)
            self.q.put(("dnd", ok, msg, parent))
        threading.Thread(target=work, daemon=True).start()

    def _compare_dialog(self):
        """Suggest the SRTs of the selected file (oldest = A, newest = B) and the media file for listening.
        The lists of the gap and the score check (every language) are no transcripts – they are left out."""
        found, media = [], None
        check_suffixes = (tuple(i18n.all_values("core.gap_suffix")) or (".gaps.srt",)) \
            + (tuple(i18n.all_values("core.score_suffix")) or (".unsure.srt",))
        for iid in self.tree.selection():
            if iid not in self.items:
                continue
            try:
                _, src, _ = core.resolve_input(self.items[iid]["path"], self.cfg)
            except Exception:
                continue
            if media is None and src.suffix.lower() in core.MEDIA_EXT:
                media = src
            found += [p for p in core.sibling_files(src, ".srt")
                      if not p.name.endswith(check_suffixes) and p not in found]
        found.sort(key=lambda p: p.stat().st_mtime)
        CompareDialog(self, str(found[0]) if found else "", str(found[-1]) if len(found) > 1 else "",
                      str(media) if media else "")

    def _ask_files(self):
        exts = " ".join("*" + e for e in core.MEDIA_EXT + (".json", ".txt"))
        files = filedialog.askopenfilenames(parent=self, title=T("main.add_title"),
                                            filetypes=[(T("main.add_types"), exts), (T("main.all_files"), "*.*")])
        self.add_files(files)

    def _remove_selected(self):
        if self._running():
            return
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.items.pop(iid, None)
        self._update_resolve_ui()

    def _clear(self):
        if self._running():
            return
        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        self._update_resolve_ui()

    def _set_state(self, iid, state, text, tag=None):
        if iid in self.items and self.tree.exists(iid):
            self.items[iid]["state"] = state
            self.tree.set(iid, "status", text)
            self.tree.item(iid, tags=(tag or state,))
            if self.items[iid].get("job"):
                self._update_resolve_ui()
                p = {"done": "done", "error": "error", "cancel": "cancelled", "wait": "queued"}.get(state)
                if p:
                    self._resolve_progress(iid, p)

    # ── Processing ─────────────────────────────────────────────────────────────

    def _running(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _job_options(self) -> core.JobOptions:
        def num(var, cast, lo, hi, name):
            try:
                v = cast(var.get().replace(",", "."))
            except ValueError:
                raise ValueError(T("main.err_number", name=name, value=var.get())) from None
            if not lo <= v <= hi:
                raise ValueError(T("main.err_range", name=name, lo=lo, hi=hi))
            return v
        pmode = self.v_pmode.get()
        return core.JobOptions(
            language      = key_of(self.lang_choices, self.v_lang.get()),
            mode          = key_of(self.mode_choices, self.v_mode.get()),
            use_filter    = self.v_filter.get(),
            prompt_mode   = pmode,
            custom_prompt = self.v_ptext.get() if pmode == "custom" else "",
            max_chars     = num(self.v_chars, int, *core.CHARS_RANGE, T("main.chars")),
            max_lines     = num(self.v_lines, int, *core.LINES_RANGE, T("main.lines")),
            keep_json     = self.v_json.get(),
            line_hold     = num(self.v_hold, float, 0, 10, T("main.hold")),
            live_lines    = self.cfg.live_lines,
        )

    def _reload_config(self) -> core.Config:
        """Re-read the INI – changes apply without a restart."""
        self.cfg = core.Config(self.ini_path)
        for w in self.cfg.warnings:
            self._append_log(f"{i18n.tag('warn')}{w}\n")
        if self.v_pmode.get() == "ini":
            self._update_prompt_field()
        return self.cfg

    def _save_defaults(self):
        """Write the current selection of the main window as default into the INI."""
        try:
            o = self._job_options()
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self)
            return
        custom = self.v_ptext.get() if o.prompt_mode == "custom" else getattr(self, "_custom_text", "")
        try:
            core.save_ini(self.ini_path, {
                ("Menu", "language"):        o.language,
                ("Menu", "mode"):            o.mode,
                ("Menu", "ffmpeg"):          "1" if o.use_filter else "0",
                ("Menu", "prompt_mode"):     o.prompt_mode,
                ("Menu", "custom_prompt"):   " ".join(custom.split()),
                ("Layout", "default_chars"): str(o.max_chars),
                ("Layout", "default_lines"): str(o.max_lines),
                ("Output", "keep_json"):     "1" if o.keep_json else "0",
                ("Lyrics", "line_hold"):     f"{o.line_hold:g}",
            })
        except OSError as e:
            messagebox.showerror(APP_NAME, T("main.save_failed", err=e), parent=self)
            return
        self._reload_config()
        self.l_status.config(text=T("main.defaults_saved"))
        self._append_log(i18n.line("info", "main.defaults_saved_log", file=self.ini_path.name) + "\n")

    def start(self):
        self._stop_countdown(silent=True)
        if self._running():
            return
        if not self.items:
            messagebox.showinfo(APP_NAME, T("main.no_files"), parent=self)
            return

        cfg = self._reload_config()
        try:
            opts = self._job_options()
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self)
            return
        self.engine.set_config(cfg)

        sel  = [i for i in self.tree.selection() if i in self.items]
        todo = sel or [i for i in self.tree.get_children() if self.items[i]["state"] != "done"] \
            or list(self.tree.get_children())
        for iid in todo:
            it = self.items[iid]
            try:   # a lyrics file may have been created in the meantime
                it["kind"], _, it["out"] = core.resolve_input(it["path"], cfg)
                self.tree.set(iid, "kind", kind_label(it["kind"], it.get("job")))
            except Exception:
                pass
            self._set_state(iid, "wait", T("status.wait"))

        self.engine.cancel.clear()
        self.b_start.state(["disabled"])
        self.b_cancel.state(["!disabled"])
        self.b_remove.state(["disabled"])
        self.b_clear.state(["disabled"])
        self.worker = threading.Thread(target=self._work, args=(todo, opts), daemon=True)
        self.worker.start()

    def cancel(self):
        self.engine.cancel.set()
        self.b_cancel.state(["disabled"])
        self.l_status.config(text=T("main.cancelling"))

    def _work(self, iids, opts):
        """Runs in the background thread – talks to the UI only through the queue."""
        ok = err = n = 0
        todo  = deque(iids)
        t_all = time.perf_counter()
        while True:
            if not todo:
                try:    # take files added during the run right away
                    todo.append(self.extra.get_nowait())
                except queue.Empty:
                    break
            iid  = todo.popleft()
            item = self.items.get(iid)
            if item is None:
                continue
            if self.engine.cancel.is_set():
                self.q.put(("state", iid, "cancel", T("status.cancelled")))
                continue
            job = item.get("job")
            if job is not None and not job.alive():      # cancelled in DaVinci Resolve meanwhile
                self.q.put(("state", iid, "cancel", T("resolve.status_cancelled")))
                continue
            n += 1
            total = n + len(todo) + self.extra.qsize()
            self.q.put(("current", iid, n, total))
            self.q.put(("log", f"\n━━ [{n}/{total}] {item['path'].name} ━━\n"))
            t = time.perf_counter()
            job_opts = opts if job is None else self._resolve_options(job)
            try:
                info = self.engine.process(item["path"], job_opts)
                # info: the real output name ({model} in the suffix) and what a hand-over to DaVinci needs
                self.q.put(("out", iid, info))
                lang = f" · {info['language']}" if info.get("language", "-") != "-" else ""
                gaps, unsure = info.get("gaps", 0), info.get("unsure", 0)
                self.q.put(("state", iid, "done",
                            Tn("status.done", info["cues"], time=fmt_dur(time.perf_counter() - t)) + lang
                            + (" · ⚠ " + Tn("status.gaps", gaps) if gaps else "")
                            + (" · ⚠ " + Tn("status.unsure", unsure) if unsure else ""),
                            "gaps" if gaps or unsure else None))
                ok += 1
            except core.Cancelled:
                self.q.put(("state", iid, "cancel", T("status.cancelled")))
            except Exception as e:
                if job is not None and not job.alive():   # the plugin removed the job folder meanwhile
                    self.q.put(("state", iid, "cancel", T("resolve.status_cancelled")))
                    continue
                if not isinstance(e, core.ToolError):
                    self.q.put(("log", traceback.format_exc()))
                self.q.put(("log", f"{i18n.tag('error')}{item['path'].name}: {e}\n"))
                self.q.put(("state", iid, "error", T("status.error")))
                err += 1
        self.q.put(("finished", ok, err, time.perf_counter() - t_all))

    def _poll(self):
        for _ in range(500):
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            kind = msg[0]
            if kind == "log":
                self._append_log(msg[1])
            elif kind == "state":
                self._set_state(*msg[1:])
                it = self.items.get(msg[1])
                if msg[2] == "done" and it and it.get("job") and self.cfg.resolve_auto and not it.get("handed"):
                    self.after(300, lambda i=msg[1]: self._hand_over([i]))
            elif kind == "current":
                iid, n, total = msg[1:]
                self.current = (n, total, self.items[iid]["kind"])
                self.current_iid = iid
                self._resolve_progress(iid, "running", stage="", pct=0)
                self._set_state(iid, "running", T("status.running"))
                self.tree.see(iid)
                self.pb.stop()
                if self.items[iid]["kind"] == "lyrics":
                    self.pb.config(mode="indeterminate")
                    self.pb.start(15)
                else:
                    self.pb.config(mode="determinate", value=0)
                self.l_status.config(text=T("main.file_n", n=n, total=total))
            elif kind == "progress":
                stage, pct = msg[1:]
                value = pct * 0.7 if stage == "asr" else 70 + pct * 0.3
                self.pb.config(mode="determinate", value=value)
                if getattr(self, "current_iid", None):
                    self._resolve_progress(self.current_iid, "running", stage=stage, pct=int(pct))
                if self.current:
                    step = T("main.step_asr" if stage == "asr" else "main.step_align")
                    self.l_status.config(text=T("main.file_n", n=self.current[0], total=self.current[1])
                                         + f" · {step} {i18n.pct(pct / 100, 0)}")
            elif kind == "finished":
                self._finished(*msg[1:])
            elif kind == "remote":
                self._remote_files(msg[1])
            elif kind == "check":
                self._show_check(*msg[1:])
            elif kind == "out":
                if msg[1] in self.items:
                    info, it = msg[2], self.items[msg[1]]
                    it.update(out=info["output"], lang=info.get("language"), mode=info.get("mode"),
                              gap_list=info.get("gap_list") or [], score_list=info.get("score_list") or [])
            elif kind == "dnd":
                ok, text, parent = msg[1:]
                (messagebox.showinfo if ok else messagebox.showerror)(
                    T("dnd.short"), text, parent=parent if parent and parent.winfo_exists() else self)
        self.after(self.POLL_MS, self._poll)

    def _finished(self, ok, err, elapsed):
        self.pb.stop()
        self.pb.config(mode="determinate", value=100 if ok else 0)
        self.b_start.state(["!disabled"])
        self.b_cancel.state(["disabled"])
        self.b_remove.state(["!disabled"])
        self.b_clear.state(["!disabled"])
        self.current = None
        text = (T("main.finished", ok=ok) + (Tn("main.finished_err", err) if err else "")
                + " · " + T("main.total", time=fmt_dur(elapsed)))
        self.l_status.config(text=text)
        self._append_log(f"\n{i18n.tag('done')}{text}\n")
        self._update_resolve_ui()
        if any(it.get("job") and it["state"] == "done" and not it.get("handed") for it in self.items.values()):
            self.l_status.config(text=T("resolve.ready_hand_over"))

        # Did something arrive right at the end? Then continue directly
        leftover = []
        while not self.extra.empty():
            leftover.append(self.extra.get_nowait())
        leftover = [i for i in leftover if i in self.items]
        if leftover and not self.engine.cancel.is_set():
            self.tree.selection_set(leftover)
            self.after(200, self.start)
            return
        self._notify()

    # ── One window for everything ──────────────────────────────────────────────

    def _start_ipc(self):
        def bind():
            for _ in range(20):          # after a restart the port may still be busy for a moment
                srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):          # Windows: port not shareable
                    srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    srv.bind(IPC_ADDR)
                    srv.listen(5)
                except OSError:
                    srv.close()
                    time.sleep(0.5)
                    continue
                self._ipc_loop(srv)
                return
            # port permanently busy – then without handing over files
        threading.Thread(target=bind, daemon=True).start()

    def _ipc_loop(self, srv):
        """Background thread: accepts files from further starts."""
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(3)
                    data = b""
                    while len(data) < 1_000_000:
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                    if not data.startswith(b"WHISPERTOOL/"):
                        continue
                    if not data.startswith(IPC_HELLO):          # another version: do not take its files
                        conn.sendall(IPC_OTHER + VERSION.encode())
                        continue
                    conn.sendall(IPC_OK)
                except OSError:
                    continue
            paths = [p for p in data[len(IPC_HELLO):].decode("utf-8", "replace").splitlines() if p.strip()]
            self.q.put(("remote", paths))

    def _remote_files(self, paths):
        """Files were dropped onto the BAT while the window is open."""
        self.deiconify()
        self.lift()
        self.focus_force()
        self.attributes("-topmost", True)
        self.after(600, lambda: self.attributes("-topmost", False))
        new = self.add_files(paths)
        if new and not self._running():
            # like a normal start: select new files, countdown or immediate start
            self.tree.selection_set(new)
            jobs = [i for i in new if self.items[i].get("job")]
            if jobs:
                self._resolve_arrived(jobs)
            elif self.cfg.menu_skip:
                self.after(400, self.start)
            elif self.cfg.menu_timeout > 0 and self.countdown is None:
                self._start_countdown(self.cfg.menu_timeout)

    def _notify(self):
        try:
            import winsound
            tada = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Media", "tada.wav")
            winsound.PlaySound(tada, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            self.bell()
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(600, lambda: self.attributes("-topmost", False))

    # ── DaVinci Resolve ────────────────────────────────────────────────────────

    def install_plugin(self, parent, on_done=None) -> bool:
        try:
            path = wt_resolve.install_plugin(VERSION, i18n.current())
        except wt_resolve.ResolveError as e:
            messagebox.showerror(T("set.tab_resolve"), str(e), parent=parent)
            return False
        except OSError as e:
            messagebox.showerror(T("set.tab_resolve"), T("set.resolve_install_failed", err=e), parent=parent)
            return False
        if on_done:
            on_done()
        self._append_log(i18n.tag("info") + T("set.resolve_installed", version=VERSION) + f" – {path}\n")
        messagebox.showinfo(T("set.tab_resolve"), T("set.resolve_installed_msg", path=path), parent=parent)
        return True

    def _plugin_on_start(self):
        status, info = wt_resolve.plugin_status(VERSION, i18n.current())
        can_ask = not self.items and not self._running() and self.countdown is None
        action = plugin_start_action(status, self.cfg.resolve_skip, can_ask)
        if action == "log":
            self._append_log(i18n.line("warn", "resolve.update_log", state=plugin_state_text(status, info)) + "\n")
        if action != "ask":
            return
        names = dict(i18n.available())
        answer = ask_buttons(self, T("set.tab_resolve"), T(
            "resolve.update_ask",
            plugin=f"{info.get('version') or '?'} · {names.get(info.get('lang'), info.get('lang') or '?')}",
            workbench=f"{VERSION} · {names.get(i18n.current(), i18n.current())}"),
            [("yes", T("resolve.update_yes")), ("later", T("resolve.update_later")), ("no", T("resolve.update_no"))])
        if answer == "yes":
            self.install_plugin(self)
        elif answer == "no":
            try:
                core.save_ini(self.ini_path, {("Resolve", "skip_plugin_update"): VERSION})
                self.cfg.resolve_skip = VERSION
            except OSError as e:
                self._append_log(i18n.tag("warn") + T("main.save_failed", err=e) + "\n")

    def _update_resolve_ui(self):
        """The hand-over button exists only in Resolve mode and works once a job is transcribed."""
        jobs = [it for it in self.items.values() if it.get("job")]
        if jobs:
            self.b_resolve.grid()
        else:
            self.b_resolve.grid_remove()
        ready = any(it["state"] == "done" for it in jobs)
        off, on, pressed = self._resolve_bg
        self.b_resolve.configure(state="normal" if ready else "disabled", background=on if ready else off,
                                 activebackground=pressed if ready else off)

    def _resolve_arrived(self, iids):
        """A job from the DaVinci Resolve plugin: own countdown (or Start by hand)."""
        self.tree.selection_set(iids)
        if self.cfg.resolve_countdown > 0:
            self._start_countdown(self.cfg.resolve_countdown, resolve=True)
        else:
            self._stop_countdown(silent=True)
            self.l_status.config(text=T("resolve.waiting_start"))

    def _resolve_options(self, job, cfg: core.Config | None = None) -> core.JobOptions:
        """
        Options of a DaVinci Resolve job: language, mode, audio filter, characters, lines and smooth display as
        chosen in the plugin, all other settings from the INI. The options of the main window do not apply.
        """
        o = core.JobOptions.from_config(cfg or self.cfg)
        def within(n, limits, default):
            return n if limits[0] <= n <= limits[1] else default
        return replace(o,
                       language=job.language if job.language in ("auto",) + core.LANGS else o.language,
                       mode=job.mode if job.mode in core.MODES else o.mode,
                       use_filter=o.use_filter if job.use_filter is None else job.use_filter,
                       max_chars=within(job.chars, core.CHARS_RANGE, o.max_chars),
                       max_lines=within(job.lines, core.LINES_RANGE, o.max_lines),
                       smooth=job.smooth)

    def _log_resolve_options(self, job):
        o = self._resolve_options(job)
        ini = " (" + T("resolve.from_ini") + ")"
        self._append_log(i18n.line(
            "info", "resolve.log_job_options",
            lang=label_of(self.lang_choices, o.language) + ("" if job.language else ini),
            mode=label_of(self.mode_choices, o.mode) + ("" if job.mode else ini),
            filter=T("resolve.filter_on" if o.use_filter else "resolve.filter_off")
            + ("" if job.use_filter is not None else ini),
            layout=T("resolve.layout", chars=o.max_chars, lines=o.max_lines)
            + ("" if (job.chars, job.lines) == (o.max_chars, o.max_lines) else ini),
            smooth=T("resolve.smooth_on" if (self.cfg.smooth if o.smooth is None else o.smooth) else "resolve.smooth_off")
            + ("" if job.smooth is not None else ini)) + "\n")
        prompt = {"ini": "resolve.prompt_ini", "custom": "resolve.prompt_custom"}.get(o.prompt_mode,
                                                                                     "resolve.prompt_none")
        self._append_log(i18n.line("info", "resolve.log_job_ini", prompt=T(prompt)) + "\n")

    def _hand_over(self, iids=None):
        """
        "Hand over to DaVinci": the given jobs, else the selected transcribed ones, else all not handed over yet.
        Afterwards the window is minimized (models stay loaded) or closed; the plugin comes to the front.
        """
        done = [i for i, it in self.items.items() if it.get("job") and it["state"] == "done"]
        sel = [i for i in self.tree.selection() if i in done]
        if iids is not None:
            targets = [i for i in iids if i in done and not self.items[i].get("handed")]
        else:
            targets = sel or [i for i in done if not self.items[i].get("handed")] or done
        handed = 0
        for iid in targets:
            it = self.items[iid]
            lang = it.get("lang")
            if not lang or lang == "-":
                lang = key_of(self.lang_choices, self.v_lang.get())
                lang = "" if lang == "auto" else lang
            try:
                res = wt_resolve.hand_over(it["job"], it["out"], lang, encoding=self.cfg.encoding,
                                           close_window=self.cfg.resolve_close_plugin,
                                           close_on_warning=self.cfg.resolve_close_warning,
                                           **self._extra_tracks(it))
            except (wt_resolve.ResolveError, OSError) as e:
                self._append_log(f"{i18n.tag('error')}{e}\n")
                messagebox.showerror(T("resolve.hand_over_title"), str(e), parent=self)
                continue
            it["handed"] = True
            self._set_state(iid, "done", Tn("resolve.status_handed", res.cues), "done")
            self._append_log(i18n.line("done", "resolve.log_handed", name=res.srt.name, n=res.cues, lang=res.lang,
                                       offset=i18n.secs(res.offset, 3)) + "\n")
            self.l_status.config(text=T("resolve.log_handed", name=res.srt.name, n=res.cues, lang=res.lang,
                                        offset=i18n.secs(res.offset, 3)))
            handed += 1
        if handed and self.cfg.resolve_close and not self._running():
            self.after(400, self._close_now)
        elif handed:
            self.after(150, self.iconify)

    def _extra_tracks(self, it) -> dict:
        """
        Own subtitle tracks of a hand-over, to check in Resolve: gaps ([Resolve] track_gaps), too fast
        subtitles ([Resolve] track_fast, mode "Sentences" only) and the unsure words of the score
        check – these whenever the check is on ([Score] threshold). Nothing to show → no track.
        """
        cfg = self.cfg
        score = it.get("score_list") if cfg.score_threshold > 0 else None
        return dict(
            gaps=it.get("gap_list") if cfg.resolve_gaps else None,
            gap_label=lambda s, e, rel: T("core.gap_cue", len=i18n.secs(e - s), db=f"{rel:+.0f}"),
            fast_cps=core.READING_CPS if cfg.resolve_fast and it.get("mode") == "sentence" else None,
            fast_label=lambda cps: T("resolve.fast_cue", cps=f"{cps:.0f}"),
            score=[(s, e, core.unsure_label(words)) for s, e, words in score] if score else None)

    def _close_now(self):
        """After a hand-over, if switched on: close without asking (nothing is running)."""
        if self._running():
            self.iconify()
            return
        self.destroy()
        os._exit(0)

    def _cleanup_resolve_jobs(self):
        """Background: remove job folders of the plugin that were cancelled, imported or left behind."""
        try:
            wt_resolve.remove_legacy_dir()          # temp/resolve/ of older versions
            n = wt_resolve.cleanup_jobs()
        except Exception:
            return
        if n:
            self.q.put(("log", i18n.tag("info") + Tn("resolve.cleanup", n) + "\n"))

    # ── Countdown ──────────────────────────────────────────────────────────────

    def _start_countdown(self, secs, resolve=False):
        """resolve: the own countdown for jobs from DaVinci Resolve (independent of [Menu] timeout)."""
        self.countdown = secs
        self.countdown_resolve = resolve
        self._countdown_run = getattr(self, "_countdown_run", 0) + 1     # a restarted countdown ticks once
        if not getattr(self, "_countdown_bound", False):
            self.bind_all("<Button>", lambda e: self._stop_countdown(), add="+")
            self.bind_all("<Key>", lambda e: self._stop_countdown(), add="+")
            self._countdown_bound = True
        self._tick(self._countdown_run)

    def _tick(self, run=None):
        if self.countdown is None or run != self._countdown_run:
            return
        if self.countdown <= 0:
            self.countdown = None
            self.start()
            return
        self.l_status.config(text=T("resolve.countdown" if self.countdown_resolve else "main.countdown",
                                    n=self.countdown))
        if self.countdown_resolve:                   # the plugin shows the countdown too
            for i, it in self.items.items():
                if it.get("job") and it["state"] == "wait":
                    self._resolve_progress(i, "countdown", n=self.countdown)
        self.countdown -= 1
        self.after(1000, lambda: self._tick(run))

    def _stop_countdown(self, silent=False):
        if self.countdown is not None:
            self.countdown = None
            if not silent:
                self.l_status.config(text=T("main.countdown_stopped"))
            for i, it in self.items.items():
                if it.get("job") and it["state"] == "wait":
                    self._resolve_progress(i, "queued")

    def _resolve_progress(self, iid, state, **values):
        """Tell the waiting plugin where a DaVinci job stands (progress.txt in its job folder)."""
        it = self.items.get(iid)
        job = it.get("job") if it else None
        if job is None or it.get("handed") or not wt_resolve:
            return
        key = (state, tuple(sorted(values.items())))
        if it.get("progress") != key:
            it["progress"] = key
            wt_resolve.write_progress(job, state, **values)

    # ── Log ────────────────────────────────────────────────────────────────────

    def _append_log(self, s: str):
        t = self.log_text
        at_end = t.yview()[1] > 0.98
        t.configure(state="normal")
        for part in re.split(r"(\r\n|\r|\n)", s):
            if part in ("\n", "\r\n"):
                t.insert("end", "\n")
            elif part == "\r":                  # progress bar: overwrite the line
                t.delete("end-1c linestart", "end-1c")
            elif (m := LIVE_RE.match(part)):      # show the recognized line live
                t.insert("end", f"  {fmt_clock(float(m[1]))} → {fmt_clock(float(m[2]))}  ", "time")
                t.insert("end", m[3].strip(), "live")
            elif part:
                t.insert("end", part, self._log_color(part))
        if int(t.index("end-1c").split(".")[0]) > 5000:
            t.delete("1.0", "1000.0")
        t.configure(state="disabled")
        if at_end:
            t.see("end")

    def _log_color(self, part: str) -> str:
        """Color by prefix ([warn], [error] … in the current language) and the symbol after it."""
        if part.startswith("━━"):
            return "head"
        if part.startswith("Traceback"):
            return "error"
        if not hasattr(self, "_prefix_kind"):
            self._prefix_kind = {i18n.tag(k).strip(): k for k in i18n.TAGS}
        m = re.match(r"(\[[^\]]+\])\s*(\S?)", part)
        if not m or m[1] not in self._prefix_kind:
            return ""
        kind, sym = self._prefix_kind[m[1]], m[2]
        if sym == "✓":
            return "ok"
        if sym == "✗":
            return "error"
        if kind == "check":
            return "warn" if sym == "!" else ""
        return {"error": "error", "done": "ok", "warn": "warn", "gap": "warn", "score": "warn"}.get(kind, "")

    # ── Explorer / open files ──────────────────────────────────────────────────

    def _reveal(self, event=None):
        iid = self.tree.identify_row(event.y) if event else (self.tree.selection() or [None])[0]
        if iid not in self.items:
            return
        out = self.items[iid]["out"]
        target = out if out.exists() else self.items[iid]["path"]
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            self._open_path(target.parent)

    def _open_folder(self):
        iids = self.tree.selection() or self.tree.get_children()
        if iids and iids[0] in self.items:
            self._open_path(self.items[iids[0]]["path"].parent)

    def _open_ini(self):
        self._open_path(self.ini_path)

    def _open_path(self, p: Path):
        try:
            if sys.platform == "win32":
                os.startfile(str(p))
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as e:
            messagebox.showerror(APP_NAME, T("main.open_failed", path=p, err=e), parent=self)

    def restart(self):
        """Restart (e.g. after changing the language) – with the same INI and the files of the list."""
        files = [str(it["path"]) for it in self.items.values()]
        py = Path(sys.executable)
        try:
            subprocess.Popen([str(py), str(Path(__file__).resolve()), "--ini", str(self.ini_path), "--fresh"] + files,
                             cwd=str(HERE), creationflags=core.NO_WINDOW)
        except OSError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self)
            return
        self.destroy()
        os._exit(0)

    def _on_close(self):
        if self._running() and not messagebox.askyesno(
                APP_NAME, T("main.quit_running"), parent=self):
            return
        self.destroy()
        os._exit(0)     # do not wait for running GPU work


# ─── Settings ─────────────────────────────────────────────────────────────────

ORIG_FILTER = "highpass=f=100,lowpass=f=8200,afftdn,dynaudnorm=f=150:g=15:p=1.0:m=10.0"


def lang_tabs() -> list:
    return [(c, T(f"speechlang.{c}")) for c in ("de", "en", "ar", "tr")] + [("*", T("speechlang.all"))]


def clean_list(text: str) -> str:
    return ", ".join(w.strip() for w in text.replace("\n", ",").split(",") if w.strip())


def clean_abbrev(text: str) -> str:
    """Like clean_list, but a line break is a space: "z.\n B." stays the one entry "z. B." """
    return ", ".join(" ".join(e.split()) for e in text.replace("\n", " ").split(",") if e.strip())


class SwitchVar:
    """Tick box + number as one INI value: without the tick "0" (off), with it the number."""

    def __init__(self, on, value):
        self.on, self.value = on, value

    def get(self) -> str:
        return self.value.get() if self.on.get() else "0"


class SettingsDialog(tk.Toplevel if tk else object):
    """Less frequently changed settings. Saving writes only what changed into the INI."""

    # (section, key): (type, min, max, row → set.<row>) – checked when saving
    NUMERIC = {
        ("Whisper", "beam_size"):      (int, 1, 50, "beam"),
        ("Whisper", "batch_size"):     (int, 1, 64, "batch"),
        ("VAD", "onset"):              (float, 0.05, 0.95, "onset"),
        ("VAD", "offset"):             (float, 0.05, 0.95, "offset"),
        ("VAD", "chunk_size"):         (int, 5, 30, "chunk"),
        ("Layout", "orphan_limit"):    (int, 0, 20, "orphan_limit"),
        ("Layout", "orphan_min_fill"): (int, 0, 100, "orphan_fill"),
        ("Layout", "pause"):           (float, 0.1, 30, "pause"),
        ("Layout", "pause_hold"):      (float, 0, 30, "pause_hold"),
        ("Layout", "max_duration"):    (float, 0, 60, "max_duration"),
        ("Lyrics", "line_gap"):        (float, 0, 2, "line_gap"),
        ("Menu", "timeout"):           (int, 0, 600, "timeout"),
        ("Resolve", "countdown"):      (int, 0, 600, "resolve_countdown"),
        ("Resolve", "close_plugin"):   (int, 0, 600, "resolve_close_plugin"),
        ("Gaps", "min_length"):        (float, 0.3, 30, "gap_min"),
        ("Gaps", "margin_db"):         (float, 2, 30, "gap_margin"),
        ("Score", "threshold"):        (float, core.SCORE_MIN, core.SCORE_MAX, "score"),
    }

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app  = app
        self.cfg  = core.Config(app.ini_path)
        self.vars: dict = {}
        self.title(f"{T('set.title')} – {APP_NAME} {VERSION}")
        self.transient(app)
        s = app.scale
        self.minsize(int(720 * s), int(620 * s))

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        nb = ttk.Notebook(body)
        nb.pack(fill="both", expand=True)
        tabs = [("whisper", self._tab_whisper), ("vad", self._tab_vad), ("subs", self._tab_subs),
                ("audio", self._tab_audio), ("langs", self._tab_langs), ("resolve", self._tab_resolve),
                ("general", self._tab_general)]
        for key, build in tabs if wt_resolve else [t for t in tabs if t[0] != "resolve"]:
            f = ttk.Frame(nb, padding=12)
            f.columnconfigure(2, weight=1)
            nb.add(f, text=T(f"set.tab_{key}"))
            build(f)
        self.orig_vars = {k: str(v.get()) for k, v in self.vars.items()}
        self.orig_lang = {code: dict(buf) for code, buf in self.lang_buf.items()}

        bar = ttk.Frame(body)
        # packed before the tabs: in a window that is too low the tabs get shorter, not the buttons
        bar.pack(side="bottom", fill="x", pady=(10, 0), before=nb)
        b_ini = ttk.Button(bar, text=T("set.open_ini"), command=lambda: app._open_path(app.ini_path))
        b_ini.pack(side="left")
        tip(b_ini, "ini_open", app.scale)
        ttk.Label(bar, text=T("set.backup_note"), foreground="#77767b").pack(side="left", padx=12)
        ttk.Button(bar, text=T("set.save"), style="Accent.TButton", command=self._save).pack(side="right")
        ttk.Button(bar, text=T("main.cancel"), command=self.destroy).pack(side="right", padx=8)
        self.update_idletasks()
        h = min(max(int(660 * s), self.winfo_reqheight()), self.winfo_screenheight() - int(80 * s))
        self.geometry(f"{int(900 * s)}x{h}+{app.winfo_rootx() + 50}+{app.winfo_rooty() + 30}")
        self.grab_set()
        self.focus_set()

    # ── Building blocks ────────────────────────────────────────────────────────

    def _var(self, sec, key, value, boolean=False):
        v = tk.BooleanVar(value=bool(value)) if boolean else tk.StringVar(value=str(value))
        self.vars[(sec, key)] = v
        return v

    def _row(self, f, r, key, widget, hint=None, wide=False):
        """Row: label set.<key>, hint set.<key>_hint (if present), tooltip tip.<key>."""
        lbl = ttk.Label(f, text=T(f"set.{key}"))
        lbl.grid(row=r, column=0, sticky="w", padx=(0, 12), pady=5)
        # wide: let a wide element span the hint column, so that the hints of the
        # other rows are not pushed out of the window
        widget.grid(row=r, column=1, columnspan=2 if wide else 1, sticky="w", pady=5)
        parts = list(widget.winfo_children()) if widget.winfo_class() == "TFrame" else [widget]
        tip([lbl] + parts, key, self.app.scale)
        if hint is None:
            hint = T(f"set.{key}_hint") if i18n.has(f"set.{key}_hint") else ""
        if hint:
            ttk.Label(f, text=hint, foreground="#77767b", wraplength=int(330 * self.app.scale),
                      justify="left").grid(row=r, column=2, sticky="w", padx=(12, 0))

    def _spin(self, f, sec, key, value, lo, hi, step=1, fmt=None):
        kw = {"format": fmt} if fmt else {}
        return ttk.Spinbox(f, from_=lo, to=hi, increment=step, width=8,
                           textvariable=self._var(sec, key, value), **kw)

    def _switch(self, f, r, key, sec, ini_key, value, default, lo, hi, step, fmt=None) -> SwitchVar:
        """
        Row "[x] text [number] unit". Without the tick the field is grey, shows the recommended value
        and the INI gets 0 (off). Rows of one INI value on two tabs share one SwitchVar.
        """
        var = self._switch_var(sec, ini_key, value, default)
        cb = ttk.Checkbutton(f, text=T(f"set.sw_{key}"), variable=var.on)
        cb.grid(row=r, column=0, sticky="w", pady=3)
        sp, unit = self._number(f, r, key, var.value, lo, hi, step, fmt)
        self._bind_state(var.on, [sp, unit])
        tip([cb, sp, unit], key, self.app.scale)
        return var

    def _switch_var(self, sec, ini_key, value, default) -> SwitchVar:
        """The SwitchVar of an INI value – the one of an earlier row if there is one."""
        var = self.vars.get((sec, ini_key))
        if not isinstance(var, SwitchVar):
            var = SwitchVar(tk.BooleanVar(value=value > 0),
                            tk.StringVar(value=f"{value if value > 0 else default:g}"))
            self.vars[(sec, ini_key)] = var
        return var

    def _switch_head(self, f, r, key, on):
        """Tick box over a whole row – its numbers follow in indented rows below (_sub_row)."""
        cb = ttk.Checkbutton(f, text=T(f"set.sw_{key}"), variable=on)
        cb.grid(row=r, column=0, columnspan=3, sticky="w", pady=(3, 0))
        return cb

    def _sub_row(self, f, r, key, var, lo, hi, step, fmt=None, indent=True):
        """Row "text [number] unit" – indented under a tick box, or a plain row (indent=False)."""
        lbl = ttk.Label(f, text=T(f"set.sw_{key}"))
        lbl.grid(row=r, column=0, sticky="w", padx=(26 if indent else 0, 0), pady=2)
        return [lbl, *self._number(f, r, key, var, lo, hi, step, fmt)]

    def _number(self, f, r, key, var, lo, hi, step, fmt=None):
        kw = {"format": fmt} if fmt else {}
        sp = ttk.Spinbox(f, from_=lo, to=hi, increment=step, width=7, textvariable=var, **kw)
        sp.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=2)
        unit = ttk.Label(f, text=T(f"set.sw_{key}_unit"))
        unit.grid(row=r, column=2, sticky="w", padx=(8, 0))
        return sp, unit

    @staticmethod
    def _bind_state(on, widgets):
        """Widgets are only active with the tick."""
        def sync(*_):
            state = ["!disabled"] if on.get() else ["disabled"]
            for w in widgets:
                w.state(state)
        on.trace_add("write", sync)
        sync()

    def _text_row(self, f, r, key, indent=True):
        """Indented line of text under a tick box – greyed out together with it."""
        lbl = ttk.Label(f, text=T(f"set.{key}"))
        lbl.grid(row=r, column=0, columnspan=3, sticky="w", padx=(26 if indent else 0, 0), pady=2)
        return lbl

    def _section(self, f, r, key):
        """Heading + line + explanation. The whole tab is one grid, so all number fields line up."""
        if not hasattr(self, "_bold"):
            self._bold = tkfont.nametofont("TkDefaultFont").copy()
            self._bold.configure(weight="bold")
        ttk.Label(f, text=T(f"set.sec_{key}"), font=self._bold).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(12 if r else 0, 0))
        ttk.Separator(f).grid(row=r + 1, column=0, columnspan=3, sticky="ew", pady=(2, 4))
        r += 2
        if i18n.has(f"set.sec_{key}_info"):
            ttk.Label(f, text=T(f"set.sec_{key}_info"), foreground="#77767b", justify="left",
                      wraplength=int(840 * self.app.scale)).grid(row=r, column=0, columnspan=3, sticky="w",
                                                                  pady=(0, 4))
            r += 1
        return r

    def _combo(self, f, sec, key, value, values, width=14):
        return ttk.Combobox(f, width=width, state="readonly", values=values, textvariable=self._var(sec, key, value))

    def _note(self, f, r, text):
        ttk.Label(f, text=text, foreground="#77767b", wraplength=int(760 * self.app.scale),
                  justify="left").grid(row=r, column=0, columnspan=3, sticky="w", pady=(14, 0))

    def _pick(self, var: tk.StringVar, folder=False):
        p = (filedialog.askdirectory(parent=self) if folder else
             filedialog.askopenfilename(parent=self, filetypes=[(T("set.programs"), "*.exe"), (T("main.all_files"), "*.*")]))
        if p:
            var.set(str(Path(p)))

    # ── Tabs ───────────────────────────────────────────────────────────────────

    def _tab_whisper(self, f):
        c = self.cfg
        mf = ttk.Frame(f)
        v_model = self._var("Whisper", "model", c.model)
        ttk.Combobox(mf, width=34, values=["large-v3", "large-v2", "large-v3-turbo"], textvariable=v_model).pack(side="left")
        ttk.Button(mf, text=T("set.folder"), command=lambda: self._pick(v_model, folder=True)).pack(side="left", padx=6)
        self._row(f, 0, "model", mf, wide=True)
        self._row(f, 1, "beam", self._spin(f, "Whisper", "beam_size", c.beam_size, 1, 50))
        self._row(f, 2, "compute", self._combo(f, "Whisper", "compute_type", c.compute_type, list(core.COMPUTE_TYPES)))
        self._row(f, 3, "batch", self._spin(f, "Whisper", "batch_size", c.batch_size, 1, 64))
        self._row(f, 4, "device", self._combo(f, "System", "device", c.device, ["cuda", "cpu"], 8))
        cb = ttk.Checkbutton(f, text=T("set.numerals"),
                             variable=self._var("Whisper", "suppress_numerals", c.suppress_numerals, True))
        cb.grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))
        tip(cb, "numerals", self.app.scale)
        self._note(f, 6, T("set.note_whisper"))

    def _tab_vad(self, f):
        c = self.cfg
        self._row(f, 0, "vad_method", self._combo(f, "VAD", "method", c.vad_method, ["pyannote", "silero"], 12))
        self._row(f, 1, "onset", self._spin(f, "VAD", "onset", f"{c.vad_onset:g}", 0.05, 0.95, 0.01, "%.3f"))
        self._row(f, 2, "offset", self._spin(f, "VAD", "offset", f"{c.vad_offset:g}", 0.05, 0.95, 0.01, "%.3f"))
        self._row(f, 3, "chunk", self._spin(f, "VAD", "chunk_size", c.chunk_size, 5, 30))

        def defaults():
            for key, val in (("method", "pyannote"), ("onset", "0.5"), ("offset", "0.363"), ("chunk_size", "30")):
                self.vars[("VAD", key)].set(val)
        b = ttk.Button(f, text=T("set.vad_defaults"), command=defaults)
        b.grid(row=4, column=1, sticky="w", pady=(10, 0))
        tip(b, "vad_defaults", self.app.scale)
        self._note(f, 5, T("set.note_vad"))

        ttk.Separator(f).grid(row=6, column=0, columnspan=3, sticky="ew", pady=12)
        for r, (key, text, val, tkey) in enumerate((
                ("enabled", T("set.gap_enabled"), c.gap_check, "gap_enabled"),
                ("write_srt", T("set.gap_srt", suffix=T("core.gap_suffix")), c.gap_srt, "gap_srt")), 7):
            cb = ttk.Checkbutton(f, text=text, variable=self._var("Gaps", key, val, True))
            cb.grid(row=r, column=0, columnspan=3, sticky="w", pady=3)
            tip(cb, tkey, self.app.scale)
        self._row(f, 9, "gap_min", self._spin(f, "Gaps", "min_length", f"{c.gap_min:g}", 0.5, 30, 0.5, "%.1f"))
        self._row(f, 10, "gap_margin", self._spin(f, "Gaps", "margin_db", f"{c.gap_margin:g}", 2, 30, 1))

        ttk.Separator(f).grid(row=11, column=0, columnspan=3, sticky="ew", pady=12)
        score = self._score_switch(f, 12)
        cb = ttk.Checkbutton(f, text=T("set.score_srt", suffix=T("core.score_suffix")),
                             variable=self._var("Score", "write_srt", c.score_srt, True))
        cb.grid(row=13, column=0, columnspan=3, sticky="w", pady=3)
        self._bind_state(score.on, [cb])
        tip(cb, "score_srt", self.app.scale)

    def _score_switch(self, f, r, key="score") -> SwitchVar:
        """Score check: the same INI value ([Score] threshold) on the tabs Speech detection and DaVinci Resolve."""
        return self._switch(f, r, key, "Score", "threshold", self.cfg.score_threshold, 0.4,
                            core.SCORE_MIN, core.SCORE_MAX, 0.05, "%.2f")

    def _tab_subs(self, f):
        """
        1 which words go into one subtitle (splitting, sentences only),
        2 pauses: one rule for all speech modes – only the length of the pause counts,
        3 lyrics with their own rules.
        """
        c, sc = self.cfg, self.app.scale
        grey = "#77767b"

        r = self._section(f, 0, "split")
        self._switch(f, r, "max_duration", "Layout", "max_duration", c.max_duration, 7, 1, 60, 0.5, "%.1f")
        on = tk.BooleanVar(value=c.orphan_limit > 0)            # short words after a comma: tick + 2 numbers
        limit = tk.StringVar(value=str(c.orphan_limit if c.orphan_limit > 0 else 4))
        self.vars[("Layout", "orphan_limit")] = SwitchVar(on, limit)
        cb = self._switch_head(f, r + 1, "orphan", on)
        w1 = self._sub_row(f, r + 2, "orphan_limit", limit, 1, 20, 1)
        w2 = self._sub_row(f, r + 3, "orphan_fill", self._var("Layout", "orphan_min_fill", c.orphan_fill), 0, 100, 5)
        self._bind_state(on, w1 + w2)
        tip([cb] + w1, "orphan_limit", sc)
        tip(w2, "orphan_fill", sc)
        cb = ttk.Checkbutton(f, text=T("set.balance_lines"),
                             variable=self._var("Layout", "balance_lines", c.balance_lines, True))
        cb.grid(row=r + 4, column=0, columnspan=3, sticky="w", pady=3)
        tip(cb, "balance_lines", sc)

        r = self._section(f, r + 5, "pause")
        w = self._sub_row(f, r, "pause", self._var("Layout", "pause", f"{c.pause:g}"), 0.5, 30, 0.5, "%.1f",
                          indent=False)
        tip(w, "pause", sc)
        ttk.Label(f, text=T("set.pause_split"), foreground=grey).grid(row=r + 1, column=0, columnspan=3, sticky="w")
        smooth = self._var("Layout", "smooth", c.smooth, True)
        cb = self._switch_head(f, r + 2, "smooth", smooth)
        w = [self._text_row(f, r + 3, "smooth_short")]
        w += self._sub_row(f, r + 4, "pause_hold", self._var("Layout", "pause_hold", f"{c.pause_hold:g}"),
                           0, 30, 0.5, "%.1f")
        self._bind_state(smooth, w)
        tip([cb] + w, "smooth", sc)
        ttk.Label(f, text=T("set.smooth_off"), foreground=grey).grid(row=r + 5, column=0, columnspan=3, sticky="w",
                                                                    pady=(2, 0))

        r = self._section(f, r + 6, "lyrics")
        w = self._sub_row(f, r, "line_gap", self._var("Lyrics", "line_gap", f"{c.line_gap:g}"), 0, 2, 0.01, "%.2f",
                          indent=False)
        tip(w, "line_gap", sc)
        self._note(f, r + 1, T("set.note_subs"))

    def _tab_audio(self, f):
        c = self.cfg
        ff = ttk.Frame(f)
        v_ff = self._var("System", "ffmpeg_path", c.raw("System", "ffmpeg_path", c.ffmpeg))
        ttk.Entry(ff, width=52, textvariable=v_ff).pack(side="left")
        ttk.Button(ff, text="…", width=3, command=lambda: self._pick(v_ff)).pack(side="left", padx=6)
        self._row(f, 0, "ffmpeg", ff, wide=True)
        af = ttk.Frame(f)
        v_flt = self._var("Audio", "filter_string", c.filter_string)
        ttk.Entry(af, width=62, textvariable=v_flt).pack(side="left")
        ttk.Button(af, text=T("set.orig_filter"), command=lambda: v_flt.set(ORIG_FILTER)).pack(side="left", padx=6)
        self._row(f, 1, "filter_string", af, wide=True)
        self._row(f, 2, "speech_suffix", ttk.Entry(f, width=16, textvariable=self._var("Output", "speech_suffix", c.speech_suffix)))
        self._row(f, 3, "lyrics_input", ttk.Entry(f, width=16, textvariable=self._var("Output", "lyrics_input", c.lyrics_input)))
        self._row(f, 4, "lyrics_suffix", ttk.Entry(f, width=16, textvariable=self._var("Output", "lyrics_suffix", c.lyrics_suffix)))
        self._row(f, 5, "encoding", self._combo(f, "Output", "encoding", c.encoding, ["utf-8", "utf-8-sig"]))

    def _tab_langs(self, f):
        c, ini = self.cfg, self.app.ini_path
        repl = lambda sec: "\n".join(f"{k} = {v}" for k, v in core.read_section_raw(ini, sec))
        self.lang_buf = {"*": {"repl": repl("Replacements")}}
        self.lang_tabs = lang_tabs()
        for code, _ in self.lang_tabs[:-1]:
            self.lang_buf[code] = {"prompt": " ".join(c.raw("Prompt", code).split()),
                                   "filler": clean_list(c.raw("Filler", code)),
                                   "dangling": clean_list(c.raw("Dangling", code)),
                                   "abbrev": clean_abbrev(c.raw("Abbreviations", code, core.ABBREVIATIONS.get(code, ""))),
                                   "repl": repl(f"Replacements.{code}"),
                                   "model": c.models.get(code, ""),
                                   "use": code in c.models and code not in c.models_off,
                                   "off_ini": code in c.models_off,
                                   "compute": c.computes.get(code, ""),
                                   "no_prompt": code in c.no_prompt}
        mono = "Consolas" if "Consolas" in tkfont.families() else "TkFixedFont"

        top = ttk.Frame(f)
        top.grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(top, text=T("main.language")).pack(side="left")
        self.v_tab_lang = tk.StringVar(value=self.lang_tabs[0][1])
        cb = ttk.Combobox(top, state="readonly", width=32, values=[l for _, l in self.lang_tabs], textvariable=self.v_tab_lang)
        cb.pack(side="left", padx=8)
        cb.bind("<<ComboboxSelected>>", lambda e: self._switch_lang())
        tip(cb, "lang_select", self.app.scale)

        f.columnconfigure(1, weight=1)
        f.rowconfigure(5, weight=1)
        def lbl(r, t, key, indent=0):
            w = ttk.Label(f, text=t + "  ⓘ")
            w.grid(row=r, column=0, sticky="nw", padx=(indent, 12), pady=(8, 0))
            tip(w, key, self.app.scale)
            return w
        lbl(1, T("main.prompt"), "lang_prompt")
        self.t_prompt = tk.Text(f, height=4, wrap="word", undo=True, font="TkDefaultFont")
        self.t_prompt.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        self.t_prompt.bind("<KeyRelease>", lambda e: self._prompt_info())
        lbl(2, T("set.filler"), "lang_filler")
        self.v_filler = tk.StringVar()
        self.e_filler = ttk.Entry(f, textvariable=self.v_filler)
        self.e_filler.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        lbl(3, T("set.dangling"), "lang_dangling")
        self.t_dangling = tk.Text(f, height=2, wrap="word", undo=True, font="TkDefaultFont")
        self.t_dangling.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        lbl(4, T("set.abbrev"), "lang_abbrev")
        self.v_abbrev = tk.StringVar()
        self.e_abbrev = ttk.Entry(f, textvariable=self.v_abbrev)
        self.e_abbrev.grid(row=4, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        lbl(5, T("set.replacements"), "lang_repl")
        rf = ttk.Frame(f)
        rf.grid(row=5, column=1, columnspan=2, sticky="nsew", pady=(8, 0))
        rf.columnconfigure(0, weight=1)
        rf.rowconfigure(0, weight=1)
        self.t_repl = tk.Text(rf, height=6, wrap="none", undo=True, font=(mono, 9))
        rsb = ttk.Scrollbar(rf, orient="vertical", command=self.t_repl.yview)
        self.t_repl.configure(yscrollcommand=rsb.set)
        self.t_repl.grid(row=0, column=0, sticky="nsew")
        rsb.grid(row=0, column=1, sticky="ns")
        # rows 7-9 belong to the own model: greyed out without the tick (_own_model_state)
        self.v_luse = tk.BooleanVar()
        self.cb_luse = ttk.Checkbutton(f, text=T("set.lang_use_model"), variable=self.v_luse)
        self.cb_luse.grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))
        tip(self.cb_luse, "lang_use_model", self.app.scale)
        self.l_lmodel = lbl(7, T("set.lang_model"), "lang_model", indent=20)
        mf = ttk.Frame(f)
        mf.grid(row=7, column=1, columnspan=2, sticky="w", pady=(8, 0))
        self.v_lmodel = tk.StringVar()
        self.e_lmodel = ttk.Entry(mf, width=44, textvariable=self.v_lmodel)
        self.e_lmodel.pack(side="left")
        self.b_lmodel = ttk.Button(mf, text=T("set.folder"), command=lambda: self._pick(self.v_lmodel, folder=True))
        self.b_lmodel.pack(side="left", padx=6)
        ttk.Label(mf, text=T("set.empty_default"), foreground="#77767b").pack(side="left", padx=6)
        self.l_lcompute = lbl(8, T("set.compute"), "lang_compute", indent=20)
        self.v_lcompute = tk.StringVar()
        self.c_lcompute = ttk.Combobox(f, state="readonly", width=18, textvariable=self.v_lcompute,
                                       values=[T("set.compute_inherit"), *core.COMPUTE_TYPES])
        self.c_lcompute.grid(row=8, column=1, sticky="w", pady=(8, 0))
        self.v_lno_prompt = tk.BooleanVar()
        self.cb_lno_prompt = ttk.Checkbutton(f, text=T("set.lang_no_prompt"), variable=self.v_lno_prompt)
        self.cb_lno_prompt.grid(row=9, column=1, columnspan=2, sticky="w", pady=(6, 0))
        tip(self.cb_lno_prompt, "lang_no_prompt", self.app.scale)
        self.l_pinfo = ttk.Label(f, foreground="#77767b")
        self.l_pinfo.grid(row=10, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self.cur_lang = "de"
        self.v_lmodel.trace_add("write", lambda *_: self._own_model_state())
        self.v_luse.trace_add("write", lambda *_: self._own_model_state())
        self._load_lang("de")

    def _tab_resolve(self, f):
        """1 plugin (installs at once, not on Save), 2 analysis tracks, 3 jobs from Resolve."""
        c, sc = self.cfg, self.app.scale
        r = self._section(f, 0, "resolve_plugin")
        # Own grid: the long tick box texts below would push column 1 far to the right.
        pg = ttk.Frame(f)
        pg.grid(row=r, column=0, columnspan=3, sticky="w")
        pf = ttk.Frame(pg)
        self.l_plugin = ttk.Label(pf, text=self._plugin_state())
        self.l_plugin.pack(side="left")
        b = ttk.Button(pf, text=T("set.resolve_install"), command=self._install_plugin)
        b.pack(side="left", padx=(14, 0))
        self._row(pg, 0, "resolve_status", pf, hint="")
        tip(b, "resolve_install", sc)
        self._row(pg, 1, "resolve_target", ttk.Label(pg, text=str(wt_resolve.scripts_dir()), foreground="#77767b",
                                                     wraplength=int(760 * sc), justify="left"), hint="")

        r = self._section(f, r + 1, "resolve_analysis")
        for i, (key, value) in enumerate((("track_gaps", c.resolve_gaps), ("track_fast", c.resolve_fast))):
            cb = ttk.Checkbutton(f, text=T(f"set.resolve_{key}"), variable=self._var("Resolve", key, value, True))
            cb.grid(row=r + i, column=0, columnspan=3, sticky="w", pady=3)
            tip(cb, f"resolve_{key}", sc)
        self._score_switch(f, r + 2, "resolve_score")

        r = self._section(f, r + 3, "resolve_jobs")
        self._switch(f, r, "resolve_countdown", "Resolve", "countdown", c.resolve_countdown, 10, 1, 600, 1)
        ttk.Label(f, text=T("set.resolve_countdown_off"), foreground="#77767b").grid(
            row=r + 1, column=0, columnspan=3, sticky="w", pady=(2, 0))
        for i, (key, value) in enumerate((("auto_hand_over", c.resolve_auto), ("close_after", c.resolve_close))):
            cb = ttk.Checkbutton(f, text=T(f"set.resolve_{key}"), variable=self._var("Resolve", key, value, True))
            cb.grid(row=r + 2 + i, column=0, columnspan=3, sticky="w", pady=(8 if i == 0 else 3, 0))
            tip(cb, f"resolve_{key}", sc)
        var = self._switch(f, r + 4, "resolve_close_plugin", "Resolve", "close_plugin", c.resolve_close_plugin,
                           10, 1, 600, 1)
        cb = ttk.Checkbutton(f, text=T("set.resolve_close_plugin_warning"),
                             variable=self._var("Resolve", "close_plugin_warning", c.resolve_close_warning, True))
        cb.grid(row=r + 5, column=0, columnspan=3, sticky="w", padx=(26, 0), pady=(2, 0))
        tip(cb, "resolve_close_plugin_warning", sc)
        self._bind_state(var.on, [cb])

    def _plugin_state(self) -> str:
        return plugin_state_text(*wt_resolve.plugin_status(VERSION, i18n.current()))

    def _install_plugin(self):
        self.app.install_plugin(self, on_done=lambda: self.l_plugin.config(text=self._plugin_state()))

    def _tab_general(self, f):
        c = self.cfg
        # interface language first – "auto" follows the Windows language (otherwise English)
        self.ui_langs = [("auto", T("set.ui_auto"))] + i18n.available()
        cur = c.raw("System", "ui_language", "auto").lower()
        v_ui = self._var("System", "ui_language", label_of(self.ui_langs, cur))
        self._row(f, 0, "ui_language", ttk.Combobox(f, width=18, state="readonly", textvariable=v_ui,
                                                    values=[l for _, l in self.ui_langs]))
        ttk.Separator(f).grid(row=1, column=0, columnspan=3, sticky="ew", pady=12)

        self._row(f, 2, "timeout", self._spin(f, "Menu", "timeout", c.menu_timeout, 0, 600))
        for r, (key, text, val, tkey) in enumerate((("skip", T("set.skip"), c.menu_skip, "skip"),
                                                     ("live_lines", T("set.live"), c.live_lines, "live")), 3):
            cb = ttk.Checkbutton(f, text=text, variable=self._var("Menu", key, val, True))
            cb.grid(row=r, column=0, columnspan=3, sticky="w", pady=4)
            tip(cb, tkey, self.app.scale)

        ttk.Separator(f).grid(row=5, column=0, columnspan=3, sticky="ew", pady=12)
        self._row(f, 6, "check", ttk.Button(f, text=T("set.check_now"),
                                           command=lambda: self.app.run_check(manual=True, parent=self)))
        if DND_FILES:
            dnd = ttk.Label(f, text=T("set.dnd_active"))
        elif core.is_installed("tkinterdnd2"):
            dnd = ttk.Label(f, text=T("set.dnd_restart"))
        else:
            dnd = ttk.Button(f, text=T("set.dnd_enable"), command=lambda: self.app.install_dnd(self))
        self._row(f, 7, "dnd", dnd, "" if DND_FILES else None)
        self._row(f, 8, "version", ttk.Label(f, text=f"{APP_NAME} {VERSION}"), str(HERE))
        self._note(f, 9, T("set.note_start"))

    # ── Languages tab ──────────────────────────────────────────────────────────

    def _store_lang(self, code):
        buf = self.lang_buf[code]
        buf["repl"] = self.t_repl.get("1.0", "end-1c")
        if code != "*":
            buf["prompt"]   = self.t_prompt.get("1.0", "end-1c")
            buf["filler"]   = self.v_filler.get()
            buf["dangling"] = self.t_dangling.get("1.0", "end-1c")
            buf["abbrev"]   = self.v_abbrev.get()
            buf["model"]    = self.v_lmodel.get()
            buf["use"]      = self.v_luse.get()
            compute = self.v_lcompute.get()
            buf["compute"]  = "" if compute == T("set.compute_inherit") else compute
            buf["no_prompt"] = self.v_lno_prompt.get()

    def _load_lang(self, code):
        buf, only_repl = self.lang_buf[code], code == "*"
        for t, key in ((self.t_prompt, "prompt"), (self.t_dangling, "dangling"), (self.t_repl, "repl")):
            t.configure(state="normal")
            t.delete("1.0", "end")
            t.insert("1.0", buf.get(key, ""))
            if only_repl and t is not self.t_repl:
                t.configure(state="disabled")
        self.v_filler.set(buf.get("filler", ""))
        self.v_abbrev.set(buf.get("abbrev", ""))
        self.v_lcompute.set(buf.get("compute") or T("set.compute_inherit"))
        self.v_lno_prompt.set(buf.get("no_prompt", False))
        self.v_luse.set(buf.get("use", False))
        self.v_lmodel.set(buf.get("model", ""))
        for w in (self.e_filler, self.e_abbrev):
            w.state(["disabled" if only_repl else "!disabled"])
        self._own_model_state()
        self._prompt_info()

    def _own_model_state(self):
        self.cb_luse.state(["disabled" if self.cur_lang == "*" else "!disabled"])
        on = self.cur_lang != "*" and self.v_luse.get()
        model = on and bool(self.v_lmodel.get().strip())
        for w, active in ((self.l_lmodel, on), (self.e_lmodel, on), (self.b_lmodel, on),
                          (self.l_lcompute, model), (self.cb_lno_prompt, model)):
            w.state(["!disabled"] if active else ["disabled"])
        self.c_lcompute.state(["!disabled", "readonly"] if model else ["disabled"])

    def _switch_lang(self):
        self._store_lang(self.cur_lang)
        self.cur_lang = key_of(self.lang_tabs, self.v_tab_lang.get())
        self._load_lang(self.cur_lang)

    def _prompt_info(self):
        if self.cur_lang == "*":
            self.l_pinfo.config(text=T("set.repl_all"))
            return
        n = len(" ".join(self.t_prompt.get("1.0", "end-1c").split()))
        warn = "  ·  " + T("set.prompt_long") if n > 600 else ""
        self.l_pinfo.config(text=T("set.prompt_chars", n=n) + warn)

    @staticmethod
    def _parse_repl(text: str, label: str) -> list[tuple[str, str]]:
        entries = []
        for i, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if not s or s[0] in ";#":
                continue
            if "=" not in s or not s.split("=", 1)[0].strip():
                raise ValueError(T("set.repl_error", lang=label, line=i, text=s))
            k, v = s.split("=", 1)
            entries.append((k.strip(), v.strip()))
        return entries

    # ── Save ───────────────────────────────────────────────────────────────────

    def _save(self):
        values, sections = {}, {}
        try:
            for key, var in self.vars.items():
                if str(var.get()) == self.orig_vars[key]:
                    continue                                    # only write what changed
                if isinstance(var, tk.BooleanVar):
                    values[key] = "1" if var.get() else "0"
                    continue
                v = var.get().strip()
                if key == ("System", "ui_language"):
                    v = key_of(self.ui_langs, v)
                if key in self.NUMERIC and not (isinstance(var, SwitchVar) and not var.on.get()):   # off = "0"
                    cast, lo, hi, row = self.NUMERIC[key]
                    name = T(f"set.{row}")
                    try:
                        n = cast(v.replace(",", "."))
                    except ValueError:
                        raise ValueError(T("main.err_number", name=name, value=v)) from None
                    if not lo <= n <= hi:
                        raise ValueError(T("main.err_range", name=name, lo=lo, hi=hi))
                    v = f"{n:g}" if cast is float else str(n)
                values[key] = v
            if self.vars[("Layout", "smooth")].get():
                try:
                    pause, hold = (float(str(self.vars[("Layout", k)].get()).replace(",", "."))
                                   for k in ("pause", "pause_hold"))
                except ValueError:
                    pause, hold = 1, 0                # an invalid number was already reported above
                if hold >= pause:
                    raise ValueError(T("set.err_hold", hold=f"{hold:g}", pause=f"{pause:g}"))
            if values.get(("Whisper", "model"), "x") == "":
                raise ValueError(T("set.model_empty"))

            self._store_lang(self.cur_lang)
            for code, buf in self.lang_buf.items():
                orig  = self.orig_lang[code]
                label = dict(self.lang_tabs)[code]
                if buf["repl"] != orig["repl"]:
                    sec = "Replacements" if code == "*" else f"Replacements.{code}"
                    sections[sec] = self._parse_repl(buf["repl"], label)
                if code == "*":
                    continue
                if buf["prompt"] != orig["prompt"]:
                    values[("Prompt", code)] = " ".join(buf["prompt"].split())
                if buf["filler"] != orig["filler"]:
                    values[("Filler", code)] = clean_list(buf["filler"])
                if buf["dangling"] != orig["dangling"]:
                    values[("Dangling", code)] = clean_list(buf["dangling"])
                if clean_abbrev(buf["abbrev"]) != orig["abbrev"]:
                    values[("Abbreviations", code)] = clean_abbrev(buf["abbrev"])
                model = buf["model"].strip()
                if model != orig["model"]:
                    values[("Whisper", f"model.{code}")] = model
                off = not buf["use"]
                if model and off != buf["off_ini"]:
                    values[("Whisper", f"use_model.{code}")] = "0" if off else "1"
                if buf["compute"] != orig["compute"]:
                    values[("Whisper", f"compute_type.{code}")] = buf["compute"]
                if buf["no_prompt"] != orig["no_prompt"]:
                    values[("Whisper", f"use_prompt.{code}")] = "0" if buf["no_prompt"] else "1"
        except ValueError as e:
            messagebox.showerror(T("set.title"), str(e), parent=self)
            return

        if not values and not sections:
            self.app.l_status.config(text=T("set.no_changes"))
            self.destroy()
            return
        try:
            core.save_ini(self.app.ini_path, values, sections)
        except OSError as e:
            messagebox.showerror(T("set.title"), T("main.save_failed", err=e), parent=self)
            return
        self.app._reload_config()
        n = len(values) + len(sections)
        self.app._append_log(i18n.tag("info") + Tn("set.saved_log", n) + "\n")
        self.app.l_status.config(text=T("set.saved"))
        self.destroy()
        if ("System", "ui_language") in values and not self.app._running() and messagebox.askyesno(
                T("set.ui_language"), T("set.restart_ask"), parent=self.app):
            self.app.restart()


# ─── Text comparison ──────────────────────────────────────────────────────────

class CompareDialog(tk.Toplevel if tk else object):
    """Compare SRTs word by word: A ↔ B, A ↔ reference or A ↔ B ↔ reference → HTML report."""

    FIELDS = (("a", "cmp_a"), ("b", "cmp_b"), ("ref", "cmp_ref"), ("media", "cmp_media"))

    def __init__(self, app: "App", a: str = "", b: str = "", media: str = ""):
        super().__init__(app)
        self.app = app
        self.title(T("cmpdlg.title"))
        self.transient(app)
        s = app.scale
        self.geometry(f"{int(800 * s)}x{int(380 * s)}+{app.winfo_rootx() + 80}+{app.winfo_rooty() + 80}")
        self.minsize(int(620 * s), int(360 * s))
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(2, weight=1)
        self.vars = {}
        start = {"a": a, "b": b, "ref": "", "media": media}
        for r, (key, tkey) in enumerate(self.FIELDS):
            chip = tk.Label(f, text=T(f"cmpdlg.{key}"), fg="white", padx=6, font=("TkDefaultFont", 9, "bold"),
                            bg={"a": "#0969da", "b": "#8250df", "ref": "#1a7f37", "media": "#656d76"}[key])
            chip.grid(row=2 * r, column=0, sticky="w", padx=(0, 8), pady=(6, 0))
            v = self.vars[key] = tk.StringVar(value=start[key])
            e = ttk.Entry(f, textvariable=v)
            e.grid(row=2 * r, column=1, columnspan=2, sticky="ew", pady=(6, 0))
            # show the file name, not only the folder – after showing and on every change
            e.bind("<Configure>", lambda _e, e=e: e.xview_moveto(1.0))
            v.trace_add("write", lambda *_, e=e: e.after_idle(lambda: e.xview_moveto(1.0)))
            ttk.Button(f, text="…", width=3, command=lambda v=v, k=key: self._pick(v, k)).grid(
                row=2 * r, column=3, padx=(6, 0), pady=(6, 0))
            if i18n.has(f"cmpdlg.{key}_hint"):
                ttk.Label(f, text=T(f"cmpdlg.{key}_hint"), foreground="#77767b").grid(row=2 * r + 1, column=1, columnspan=2, sticky="w")
            tip((chip, e), tkey, s)
        ttk.Label(f, foreground="#77767b", wraplength=int(760 * s), justify="left", text=T("cmpdlg.info")).grid(
            row=8, column=0, columnspan=4, sticky="w", pady=(12, 0))
        bar = ttk.Frame(f)
        bar.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        self.l_busy = ttk.Label(bar, foreground="#77767b")
        self.l_busy.pack(side="left")
        self.b_run = ttk.Button(bar, text=T("cmpdlg.run"), style="Accent.TButton", command=self._run)
        self.b_run.pack(side="right")
        ttk.Button(bar, text=T("main.cancel"), command=self.destroy).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: self._run())
        self.grab_set()
        self.focus_set()

    def _pick(self, var, key):
        start = Path(var.get() or self.vars["a"].get() or ".").expanduser()
        types = ([(T("cmpdlg.type_media"), " ".join("*" + e for e in core.MEDIA_EXT)), (T("main.all_files"), "*.*")]
                 if key == "media" else [(T("cmpdlg.type_srt"), "*.srt"), (T("main.all_files"), "*.*")])
        p = filedialog.askopenfilename(parent=self, initialdir=str(start.parent if start.suffix else start),
                                       filetypes=types)
        if p:
            var.set(str(Path(p)))

    def _run(self):
        a, b, ref, media = (self.vars[k].get().strip().strip('"') for k in ("a", "b", "ref", "media"))
        if not b and not ref:
            messagebox.showerror(T("cmpdlg.title"), T("cmpdlg.need_two"), parent=self)
            return
        for key, p in (("a", a), ("b", b), ("ref", ref), ("media", media)):
            if (key == "a" or p) and not Path(p).is_file():
                messagebox.showerror(T("cmpdlg.title"), T("cmpdlg.not_found", label=T(f"cmpdlg.{key}"), path=p),
                                     parent=self)
                return
        self.b_run.state(["disabled"])
        self.configure(cursor="watch")
        self.l_busy.config(text=T("cmpdlg.busy_audio" if media else "cmpdlg.busy"))
        out = wt_compare.report_name(a, b or ref)
        ffmpeg = self.app.cfg.ffmpeg
        self._result = None

        def work():          # in the background – long speeches take a few seconds
            try:
                m = media
                if m and Path(m).suffix.lower() not in wt_compare.AUDIO_EXT:
                    m = wt_compare.make_audio_preview(m, out.with_suffix(".m4a"), ffmpeg) or m
                res = wt_compare.compare(a, b or None, ref or None, media=m or None, report_dir=out.parent)
                self._result = (res, wt_compare.write_report(res, out), None)
            except Exception as e:
                self._result = (None, None, e)
        threading.Thread(target=work, daemon=True).start()
        self._wait()

    def _wait(self):
        if self._result is None:
            self.after(150, self._wait)
            return
        res, out, err = self._result
        if err:
            self.configure(cursor="")
            self.b_run.state(["!disabled"])
            self.l_busy.config(text="")
            messagebox.showerror(T("cmpdlg.title"), T("cmpdlg.failed", err=err), parent=self)
            return
        names = " ↔ ".join(d.path.name for d in (res.a, res.b, res.r) if d)
        tag = i18n.tag("compare")
        self.app._append_log(f"{tag}{names}: {wt_compare.summary(res)}\n{tag}{T('cmpdlg.report', path=out)}\n")
        self.app.l_status.config(text=T("cmpdlg.status", text=wt_compare.summary(res, short=True)[:90]))
        self.destroy()
        self.app._open_path(out)


# ─── Without window ───────────────────────────────────────────────────────────

def run_console(ini: Path, files: list[str]) -> int:
    ensure_ini(ini)
    print(i18n.line("info", "main.version", app=APP_NAME, version=VERSION, folder=HERE))
    cfg = core.Config(ini)
    for w in cfg.warnings:
        print(f"{i18n.tag('warn')}{w}")
    opts   = core.JobOptions.from_config(cfg)
    engine = core.Engine(cfg)
    ok = err = 0
    t_all = time.perf_counter()
    for n, f in enumerate(files, 1):
        print(f"\n━━ [{n}/{len(files)}] {Path(f).name} ━━")
        try:
            engine.process(f, opts)
            ok += 1
        except Exception as e:
            if not isinstance(e, core.ToolError):
                traceback.print_exc()
            print(f"{i18n.tag('error')}{Path(f).name}: {e}")
            err += 1
    print(f"\n{i18n.tag('done')}" + T("main.finished", ok=ok) + (Tn("main.finished_err", err) if err else "")
          + " · " + T("main.total", time=fmt_dur(time.perf_counter() - t_all)))
    return 0 if err == 0 else 1


def _dpi_aware():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description=f"{APP_NAME} – {T('cli.description')}")
    ap.add_argument("files", nargs="*", help=T("cli.files"))
    ap.add_argument("--nogui", action="store_true", help=T("cli.nogui"))
    ap.add_argument("--ini", default=str(DEFAULT_INI), help=T("cli.ini"))
    ap.add_argument("--fresh", action="store_true", help=argparse.SUPPRESS)     # restart: do not hand over
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    args = ap.parse_args()
    ensure_ini(Path(args.ini))

    if args.nogui:
        sys.exit(run_console(Path(args.ini), args.files))
    if tk is None:
        _fatal(T("app.err_tkinter"))
        sys.exit(1)

    state, other = ("none", "") if args.fresh else send_to_running(args.files)
    if state == "ok":
        return          # window of this version is already open – the files were handed over to it
    _dpi_aware()
    app = App(Path(args.ini), args.files)
    if state == "other":
        # an older/other version is still open: say so clearly instead of silently showing the old window
        msg = T("main.other_instance", version=other) if other else T("main.other_instance_old")
        app._append_log(f"{i18n.tag('warn')}{msg.splitlines()[0]}\n")
        app.after(800, lambda: messagebox.showwarning(APP_NAME, msg, parent=app))
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        ERROR_LOG.write_text(tb, encoding="utf-8")
        try:
            messagebox.showerror(APP_NAME, f"{tb[-1500:]}\n\nDetails: {ERROR_LOG}")
        except Exception:
            print(tb, file=sys.__stderr__)
