"""
Window of install.bat and uninstall.bat: python wt_setup_ui.py install | uninstall.
The decisions and building blocks are in wt_setup.py; this file shows them and runs the steps in order.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import wt_i18n as i18n  # noqa: E402
import wt_setup as s  # noqa: E402
from wt_i18n import T, Tn  # noqa: E402

try:
    import tkinter as tk
    from tkinter import filedialog, font as tkfont, ttk
except ImportError:
    tk = None

KIT = ("install.bat", "uninstall.bat", "wt_setup.py", "wt_setup_ui.py", "wt_i18n.py", "wt_resolve.py",
       "wt_version.py", s.MANIFEST)
CRITICAL = ("torch", "torchaudio", "torchvision", "torchcodec", "ctranslate2", "whisperx", "faster-whisper")
SHOWN_FOREIGN = 50
STILL_ACTIVE = 259


def fmt_size(n: int) -> str:
    if n >= 1e9:
        return T("setup.gb", n=i18n.num(n / 1e9, 1))
    return T("setup.mb", n=i18n.num(max(n, 0) / 1e6, 1 if n < 1e7 else 0))


def open_path(p):
    try:
        if sys.platform == "win32":
            os.startfile(str(p))
        else:
            subprocess.Popen(["xdg-open", str(p)])
    except OSError:
        pass


def pid_alive(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return code.value == STILL_ACTIVE


def keep_awake(on: bool):
    """No standby during the installation (SetThreadExecutionState of the calling thread)."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if on else 0))
        except Exception:
            pass


# ─── Analysis ────────────────────────────────────────────────────────────────

def choose_target(source: Path) -> tuple[Path, str]:
    """→ (target folder, inplace | copy). A folder with an installation is repaired where it is."""
    if s.is_installation(source):
        return source, "inplace"
    reg = s.registered_location()
    target = reg if reg else s.default_target()
    if os.path.normcase(str(target)) == os.path.normcase(str(source)):
        return source, "inplace"
    return target, "copy"


def analyse_install(source: Path, target: Path, mode: str, online: bool = True) -> dict:
    a = {"source": source, "target": target, "mode": mode}
    a["source_version"] = s.read_version(source)
    a["target_version"] = s.read_version(target) if mode == "copy" and s.is_installation(target) else ""
    a["older_source"] = bool(a["target_version"]) and s.newer(a["target_version"], a["source_version"])
    a["running"] = s.workbench_running()
    a["admin"], a["arm"] = s.is_admin(), s.is_arm()
    a["writable"] = s.writable(target)
    a["path_warnings"] = s.path_warnings(target, long_paths=s.long_paths_enabled())
    a["free"] = s.free_gb(target)
    a["winget"] = bool(s.winget())
    a["pythons"] = s.find_pythons()
    a["python"] = s.choose_python(a["pythons"])
    a["gpus"], a["no_driver"] = s.find_gpus()
    a["build"], a["gpu"] = s.choose_build(a["gpus"])
    major = s.driver_major(a["gpu"].driver) if a["gpu"] else None
    a["driver_ok"] = major is None or major >= s.DRIVER_MIN
    a["ffmpeg"] = s.find_ffmpeg(target if s.is_installation(target) else source)
    a["vcredist"] = s.vcredist_installed()
    a["resolve"] = s.resolve_installed()
    a["plugin"] = s.plugin_of(target)
    a["model"] = s.ini_value(target, "Whisper", "model", "large-v3")
    a["internet"] = s.internet() if online else True
    a["release"] = None
    if online and a["internet"]:
        rel = s.latest_release()
        newest = max([v for v in (a["source_version"], a["target_version"]) if v] or ["0"], key=s.version_key)
        if rel and s.newer(rel["version"], newest):
            a["release"] = rel
    req_folder = source
    a["pins"] = s.read_pins(req_folder / "constraints.txt")
    a["venv"] = s.inspect_venv(target)
    a["state"] = s.read_state(target)
    report = None
    v = a["venv"]
    if online and a["internet"] and v.exists and not v.problem and v.pip >= s.PIP_REPORT:
        out = s._output(s.dry_run_command(s.venv_python(target), req_folder), timeout=300, env=s.clean_env())
        try:
            report = s.parse_pip_report(out or "")
        except (ValueError, KeyError, TypeError):
            report = None
    parent = (target.parent / "venv").is_dir() and not (target / "venv").exists()
    a["scenario"] = s.classify(v, a["state"], a["pins"], a["build"], s.requirements_hash(req_folder), parent, report)
    return a


def blockers(a: dict) -> list[str]:
    out = []
    if a["running"]:
        out.append("running")
    if a["arm"]:
        out.append("arm")
    if not a["writable"]:
        out.append("not_writable")
    if a["free"] < s.DISK_GB and a["scenario"].code != "ok":
        out.append("disk_low")
    if not a["internet"] and a["scenario"].code != "ok":
        out.append("internet")
    if a["python"] is None and not a["winget"]:
        out.append("python_manual")
    if a["no_driver"]:
        out.append("gpu_no_driver")
    return out


# ─── The window ──────────────────────────────────────────────────────────────

class Wizard(tk.Tk if tk else object):
    def __init__(self, title: str):
        super().__init__()
        self.title(title)
        self.scale = max(1.0, self.winfo_fpixels("1i") / 96)
        self.geometry(f"{int(840 * self.scale)}x{int(620 * self.scale)}")
        self.minsize(int(620 * self.scale), int(460 * self.scale))
        style = ttk.Style(self)
        if sys.platform != "win32" and "clam" in style.theme_names():
            style.theme_use("clam")
        base = tkfont.nametofont("TkDefaultFont")
        self.bold = base.copy()
        self.bold.configure(weight="bold")
        self.head_font = base.copy()
        self.head_font.configure(weight="bold", size=int(base.cget("size") * 1.35) if base.cget("size") > 0 else 13)
        self.q: queue.Queue = queue.Queue()
        self.log_lines: list[str] = []
        self.log_file: Path | None = None

        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        self.head = ttk.Label(outer, font=self.head_font)
        self.head.pack(anchor="w")
        self.sub = ttk.Label(outer, foreground="#555555", wraplength=int(700 * self.scale), justify="left")
        self.sub.pack(anchor="w", pady=(2, 8))
        self.body_holder = ttk.Frame(outer)
        self.body_holder.pack(fill="both", expand=True)
        self.details = tk.Text(outer, height=9, wrap="none", font=("Consolas", 9) if sys.platform == "win32" else
                               "TkFixedFont", state="disabled", background="#1e1e1e", foreground="#d4d4d4")
        self.buttons = ttk.Frame(outer)
        self.buttons.pack(fill="x", side="bottom", pady=(10, 0))
        self.b_details = ttk.Button(self.buttons, text=T("setup.btn_details"), command=self.toggle_details)
        self.b_details.pack(side="left")
        self.body = None
        self._poll_id = self.after(100, self._poll)
        self.bg = style.lookup("TFrame", "background") or self.cget("background")

    def destroy(self):
        try:
            self.after_cancel(self._poll_id)
        except Exception:
            pass
        super().destroy()

    # ── Building blocks ──

    def page(self, head: str, sub: str = "", scroll: bool = True) -> ttk.Frame:
        if self.body is not None:
            self.body.destroy()
        for w in self.buttons.winfo_children():
            if w is not self.b_details:
                w.destroy()
        self.head.config(text=head)
        self.sub.config(text=sub)
        self.body = ttk.Frame(self.body_holder)
        self.body.pack(fill="both", expand=True)
        if not scroll:
            return self.body
        canvas = tk.Canvas(self.body, highlightthickness=0, borderwidth=0, background=self.bg)
        bar = ttk.Scrollbar(self.body, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        inner.columnconfigure(1, weight=1)
        self._row = 0
        return inner

    def section(self, parent, text: str):
        ttk.Label(parent, text=text, font=self.bold).grid(row=self._row, column=0, columnspan=3, sticky="w",
                                                         pady=(12 if self._row else 0, 4))
        self._row += 1

    def line(self, parent, icon: str, text: str, color: str = "", widget=None, indent: int = 0):
        colors = {"✓": "#26a269", "⚠": "#b35c00", "✗": "#c01c28", "•": "#555555"}
        ttk.Label(parent, text=icon, foreground=colors.get(icon, color or "")).grid(
            row=self._row, column=0, sticky="nw", padx=(indent, 6))
        ttk.Label(parent, text=text, wraplength=int(560 * self.scale), justify="left").grid(
            row=self._row, column=1, sticky="w")
        if widget is not None:
            widget.grid(row=self._row, column=2, sticky="e", padx=(8, 0))
        self._row += 1

    def check(self, parent, text: str, value: bool, indent: int = 0) -> tk.BooleanVar:
        """A tick box whose text wraps (ttk.Checkbutton has no wraplength); a click on the text toggles it."""
        var = tk.BooleanVar(value=value)
        row = ttk.Frame(parent)
        row.grid(row=self._row, column=0, columnspan=3, sticky="ew", padx=(indent, 0), pady=1)
        ttk.Checkbutton(row, variable=var).pack(side="left", anchor="n")
        lbl = ttk.Label(row, text=text, wraplength=int(620 * self.scale - indent), justify="left")
        lbl.pack(side="left", anchor="w", fill="x")
        lbl.bind("<Button-1>", lambda e: var.set(not var.get()))
        self._row += 1
        return var

    def text_block(self, parent, text: str, height: int = 6):
        t = tk.Text(parent, height=height, wrap="word", relief="flat", background=self.bg)
        t.insert("1.0", text)
        t.configure(state="disabled")
        t.grid(row=self._row, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
        self._row += 1
        return t

    def button(self, text: str, command, accent: bool = False, side: str = "right"):
        """accent: the blue button the texts refer to (a tk button: the Windows theme ignores colors of ttk)."""
        if accent:
            b = tk.Button(self.buttons, text=text, command=command, font=self.bold, relief="flat", bd=0, padx=16,
                          pady=5, cursor="hand2", fg="white", background="#1a66d2", activebackground="#1557b0",
                          activeforeground="white", disabledforeground="#d0d0d0")
        else:
            b = ttk.Button(self.buttons, text=text, command=command)
        b.pack(side=side, padx=(8, 0), fill="y")
        return b

    def toggle_details(self):
        if self.details.winfo_ismapped():
            self.details.pack_forget()
        else:
            self.details.pack(fill="x", before=self.buttons, pady=(8, 0))

    def log(self, line: str):
        self.q.put(("log", line))

    def _write_log(self, line: str):
        stamp = time.strftime("%H:%M:%S")
        self.log_lines.append(f"{stamp} {line}")
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(f"{stamp} {line}\n")
            except OSError:
                pass
        self.details.configure(state="normal")
        self.details.insert("end", line + "\n")
        if int(self.details.index("end-1c").split(".")[0]) > 3000:
            self.details.delete("1.0", "500.0")
        self.details.see("end")
        self.details.configure(state="disabled")

    def set_log_file(self, path: Path):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(self.log_lines) + "\n")
            self.log_file = path
        except OSError:
            self.log_file = None

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "log":
                    self._write_log(msg[1])
                elif msg[0] == "call":
                    msg[1](*msg[2:])
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def in_ui(self, fn, *args):
        self.q.put(("call", fn, *args))

    def background(self, work, done):
        def run():
            try:
                result = work()
            except Exception as e:
                self.log(traceback.format_exc())
                result = e
            self.in_ui(done, result)
        threading.Thread(target=run, daemon=True).start()

    def checking(self, text: str):
        f = self.page(text, scroll=False)
        pb = ttk.Progressbar(f, mode="indeterminate")
        pb.pack(fill="x", pady=20)
        pb.start(12)
        self.button(T("setup.btn_cancel"), self.destroy)


# ─── Installation ────────────────────────────────────────────────────────────

class InstallWindow(Wizard):
    def __init__(self, source: Path, resume: bool = False):
        super().__init__(T("setup.title_install", app=s.APP_NAME))
        self.source = source
        self.target, self.mode = choose_target(source)
        self.resume = resume
        self.a: dict = {}
        self.choices: dict = {}
        self.result: dict = {}
        self.start_analysis()

    def start_analysis(self):
        self.checking(T("setup.checking"))
        self.log(f"{s.APP_NAME} {s.read_version(self.source)} · {self.source} → {self.target} ({self.mode})")
        self.background(lambda: analyse_install(self.source, self.target, self.mode, online=not self.resume),
                        self.analysed)

    def analysed(self, a):
        if isinstance(a, Exception):
            self.show_error(s.SetupError("generic", str(a)))
            return
        self.a = a
        self._log_analysis(a)
        if self.resume:
            pending = a["state"].get("pending") or {}
            if not blockers(a):
                self.run_steps(pending)
                return
        self.overview()

    def _log_analysis(self, a):
        py = a["python"]
        self.log(f"python: {py.path if py else '-'} {py.version if py else ''}; found: "
                 + ", ".join(f"{p.path} {p.version} {p.problem() or 'ok'}" for p in a["pythons"]))
        self.log(f"gpu: {[(g.name, g.driver, g.cc) for g in a['gpus']]} → {a['build']}; no driver: {a['no_driver']}")
        self.log(f"ffmpeg: {a['ffmpeg']}; vcredist: {a['vcredist']}; free: {a['free']:.1f} GB; internet: "
                 f"{a['internet']}; admin: {a['admin']}; paths: {a['path_warnings']}")
        sc = a["scenario"]
        self.log(f"venv: exists={a['venv'].exists} problem={a['venv'].problem} → {sc.code}, torch={sc.torch}, "
                 f"report={sc.report}, changes={[(c.name, c.old, c.new, c.kind) for c in sc.changes]}")
        if a["release"]:
            self.log(f"release on GitHub: {a['release']}")

    # ── Overview ──

    def overview(self):
        a, sc = self.a, self.a["scenario"]
        update = a["mode"] == "copy" and a["target_version"]
        head = T("setup.head_update") if update or a["release"] else \
            T("setup.head_repair") if sc.code not in ("new", "parent") else T("setup.head_install")
        f = self.page(head, T("setup.intro"))
        self.vars = {}

        self.section(f, T("setup.section_folder"))
        if a["mode"] == "copy" and not a["target_version"]:
            b = ttk.Button(f, text=T("setup.folder_change"), command=self.change_folder)
            self.line(f, "•", T("setup.folder_new", path=a["target"]), widget=b)
        elif update:
            self.line(f, "•", T("setup.folder_update", path=a["target"], old=a["target_version"],
                                new=a["source_version"]))
        else:
            self.line(f, "•", T("setup.folder_existing", path=a["target"],
                                version=s.read_version(a["target"]) or "?"))
        if a["older_source"]:
            self.line(f, "⚠", T("setup.older_source", installed=a["target_version"], source=a["source_version"]))
            self.vars["older"] = self.check(f, T("setup.ex_older", source=a["source_version"]), False, 22)
        for code in a["path_warnings"]:
            self.line(f, "⚠", T(f"setup.path_{code}", n=s.LONG_PATH) + " " + T("setup.path_hint",
                                                                            default=s.default_target()))
        if a["release"]:
            rel = a["release"]
            b = ttk.Button(f, text=T("setup.btn_whatsnew"), command=lambda: open_path(rel["page"] or s.PROJECT_URL))
            self.line(f, "⚠", T("setup.update_available", new=rel["version"],
                                old=a["target_version"] or a["source_version"]), widget=b)
            self.vars["online"] = self.check(f, T("setup.ex_update", version=rel["version"]), True, 22)

        self.section(f, T("setup.section_pc"))
        for code in blockers(a):
            widget = None
            if code == "running":
                widget = ttk.Button(f, text=T("setup.btn_check_again"), command=self.start_analysis)
            elif code == "python_manual":
                widget = ttk.Button(f, text=T("setup.btn_python"), command=lambda: open_path(s.PYTHON_URL))
            elif code == "gpu_no_driver":
                widget = ttk.Button(f, text=T("setup.btn_driver"), command=lambda: open_path(s.DRIVER_URL))
            self.line(f, "✗", T(f"setup.block_{code}", path=a["target"], default=s.default_target(),
                                free=i18n.num(a["free"], 0), need=s.DISK_GB), widget=widget)
        if a["admin"]:
            self.line(f, "⚠", T("setup.admin"))
        py = a["python"]
        if py:
            self.line(f, "✓", T("setup.python_ok", version=py.text))
        elif a["winget"]:
            found = "; ".join(T(f"setup.py_{p.problem()}", version=p.text) for p in a["pythons"] if p.problem())
            self.line(f, "⚠", T("setup.python_winget") + (" " + found if found else ""))
        gpu = a["gpu"]
        if gpu and a["build"] == "cu128":
            self.line(f, "✓", T("setup.gpu_ok", name=gpu.name))
        elif gpu and a["build"] == "cu126":
            self.line(f, "⚠", T("setup.gpu_old", name=gpu.name))
        elif not a["no_driver"]:
            self.line(f, "⚠", T("setup.gpu_none"))
        if gpu and not a["driver_ok"]:
            b = ttk.Button(f, text=T("setup.btn_driver"), command=lambda: open_path(s.DRIVER_URL))
            self.line(f, "⚠", T("setup.driver_old", version=gpu.driver, need=s.DRIVER_MIN), widget=b)
        elif gpu:
            self.line(f, "✓", T("setup.driver_ok", version=gpu.driver))
        path, ver, major = a["ffmpeg"]
        if ver and s.ffmpeg_ok(ver, major):
            self.line(f, "✓", T("setup.ffmpeg_ok", version=ver))
        else:
            self.line(f, "⚠", T("setup.ffmpeg_old", version=ver) if ver else T("setup.ffmpeg_missing"))
            if a["winget"]:
                self.vars["ffmpeg"] = self.check(f, T("setup.ffmpeg_winget"), True, 22)
            else:
                self.line(f, "•", T("setup.ffmpeg_manual"), indent=22)
        if not a["vcredist"]:
            self.line(f, "⚠", T("setup.vcredist_missing"))
            if a["winget"]:
                self.vars["vcredist"] = self.check(f, T("setup.vcredist_winget"), True, 22)
        if a["free"] >= s.DISK_GB:
            self.line(f, "✓", T("setup.disk_ok", free=i18n.num(a["free"], 0)))

        self.section(f, T("setup.section_env"))
        self._scenario_lines(f)

        st = a["state"]
        if sc.code == "ok" and not a["release"] and a["mode"] == "inplace":
            self._overview_buttons()
            return
        self.section(f, T("setup.section_extras"))
        self.vars["desktop"] = self.check(f, T("setup.ex_desktop"), st.get("desktop", True))
        self.vars["startmenu"] = self.check(f, T("setup.ex_startmenu"), st.get("startmenu", True))
        self.vars["model"] = self.check(f, T("setup.ex_model", model=a["model"]),
                                        st.get("model", True) and sc.code != "ok")
        if a["resolve"] or a["plugin"]:
            self.vars["plugin"] = self.check(f, T("setup.ex_plugin"), True)

        self._overview_buttons()

    def _scenario_lines(self, f):
        a, sc = self.a, self.a["scenario"]
        v = a["venv"]
        if sc.code == "broken":
            reason = T(f"setup.broken_{v.problem}", home=s.read_pyvenv_cfg(a["target"] / "venv").get("home", ""),
                       version=v.info.text if v.info else "") if i18n.has(f"setup.broken_{v.problem}") \
                else T(f"setup.py_{v.problem}", version=v.info.text if v.info else "")
            self.line(f, "⚠", T("setup.scen_broken", reason=reason))
            return
        if sc.code in ("new", "parent", "ok"):
            self.line(f, "✓" if sc.code == "ok" else "•", T(f"setup.scen_{sc.code}", path=a["target"].parent / "venv"))
            return
        if sc.code == "incomplete":
            self.line(f, "⚠", T("setup.scen_incomplete", date=a["state"].get("started", "")))
        groups = s.by_kind(sc.changes)
        real = sum(len(v) for k, v in groups.items() if k != "foreign")
        if real:
            self.line(f, "•", Tn("setup.scen_changes", real))
        if not sc.report:
            self.line(f, "•", T("setup.scen_offline"))
        for kind in ("downgrade", "rebuild", "update", "new", "foreign"):
            items = groups.get(kind, [])
            if not items:
                continue
            icon = "⚠" if kind in ("downgrade", "rebuild", "foreign") else "•"
            self.line(f, icon, T(f"setup.ch_{kind}", n=len(items)))
            shown = items if kind != "new" or len(items) <= 12 else items[:12]
            text = "\n".join(T("setup.ch_line", name=c.name, old=c.old or "–", new=c.new or "–") for c in shown)
            if len(shown) < len(items):
                text += "\n" + T("setup.ch_more", n=len(items) - len(shown))
            self.line(f, "", text, indent=22)
            if kind == "downgrade":
                critical = any(s.norm_name(c.name) in CRITICAL for c in items)
                self.line(f, "", T("setup.warn_downgrade_critical" if critical else "setup.warn_downgrade"), indent=22)
            if kind == "rebuild":
                self.line(f, "", T("setup.warn_rebuild"), indent=22)
            if kind == "foreign":
                self.line(f, "", T("setup.warn_foreign"), indent=22)

    def _overview_buttons(self):
        a, sc = self.a, self.a["scenario"]
        blocked = bool(blockers(a))
        self.button(T("setup.btn_cancel"), self.destroy)
        if sc.code == "ok" and not a["release"] and a["mode"] == "inplace":
            self.button(T("setup.btn_start"), self.start_workbench, accent=True)
            self.button(T("setup.btn_repair"), lambda: self.confirm(repair=True))
            self.button(T("setup.btn_recreate"), lambda: self.confirm(recreate=True))
            return
        key = {"new": "install", "parent": "install", "broken": "recreate", "incomplete": "continue",
               "changes": "adjust", "ok": "repair"}[sc.code]
        if a["release"] or (a["mode"] == "copy" and a["target_version"]):
            key = "update"
        b = self.button(T(f"setup.btn_{key}"), lambda: self.confirm(recreate=sc.code == "broken"), accent=True)
        if blocked:
            b.configure(state="disabled", background="#9db8e0")
        if sc.code in ("changes", "incomplete"):
            self.button(T("setup.btn_recreate"), lambda: self.confirm(recreate=True)).configure(
                state="disabled" if blocked else "normal")

    def change_folder(self):
        chosen = filedialog.askdirectory(parent=self, title=T("setup.folder_pick"), initialdir=str(self.target.parent))
        if not chosen:
            return
        p = Path(chosen)
        if p.name != s.APP_DIR and any(p.iterdir()):
            p = p / s.APP_DIR
        self.target, self.mode = p, "copy" if p != self.source else "inplace"
        self.start_analysis()

    def confirm(self, recreate=False, repair=False):
        a = self.a
        if a["older_source"] and not self.vars.get("older", tk.BooleanVar(value=False)).get():
            self.sub.config(text=T("setup.older_blocked", path=a["target"]))
            return
        c = {k: bool(a["state"].get(k, True)) for k in ("desktop", "startmenu")}
        c.update({k: bool(v.get()) for k, v in self.vars.items()})
        c.update(recreate=recreate or a["scenario"].code == "broken", repair=repair)
        self.run_steps(c)

    # ── Steps ──

    def plan(self, c: dict) -> list[str]:
        a, sc = self.a, self.a["scenario"]
        steps = []
        if c.get("online") and a["release"]:
            steps.append("download")
        if a["mode"] == "copy" and (not a["older_source"] or c.get("older")):
            steps.append("copy")
            return steps                    # the rest runs from the new folder, with its own files
        if a["python"] is None:
            steps.append("python")
        if c.get("ffmpeg"):
            steps.append("ffmpeg")
        if c.get("vcredist"):
            steps.append("vcredist")
        if sc.code in ("new", "parent", "broken") or c.get("recreate"):
            steps.append("venv")
        steps += ["pip", "torch", "packages", "dnd", "verify", "ini", "extras"]
        if c.get("model"):
            steps.append("model")
        if c.get("plugin") or (a["plugin"] and a["scenario"].code != "ok"):
            steps.append("plugin")
        return steps

    def run_steps(self, c: dict):
        self.choices = c
        steps = self.plan(c)
        f = self.page(T("setup.run_head"), T("setup.run_note"), scroll=False)
        self.step_labels = {}
        for st in steps:
            row = ttk.Frame(f)
            row.pack(fill="x", pady=1)
            icon = ttk.Label(row, text="○", width=2)
            icon.pack(side="left")
            ttk.Label(row, text=self.step_text(st)).pack(side="left")
            self.step_labels[st] = icon
        self.activity = ttk.Label(f, foreground="#555555")
        self.activity.pack(anchor="w", pady=(12, 2))
        self.pb = ttk.Progressbar(f, mode="indeterminate")
        self.pb.pack(fill="x")
        self.pb.start(12)
        self.button(T("setup.btn_cancel"), self.ask_cancel)
        if self.target.exists():
            self.set_log_file(self.target / s.LOG_FILE)
        self.protocol("WM_DELETE_WINDOW", self.ask_cancel)
        self.cancelled = False
        self.background(lambda: self.worker(steps, c), self.finished)

    def step_text(self, st: str) -> str:
        a = self.a
        version = (a.get("release") or {}).get("version", "")
        return T(f"setup.step_{st}", version=version, path=self.target, model=a.get("model", ""))

    def ask_cancel(self):
        from tkinter import messagebox
        if messagebox.askyesno(T("setup.cancel_title"), T("setup.cancel_text"), parent=self):
            self.cancelled = True
            self.destroy()
            os._exit(1)

    def step_state(self, st: str, icon: str):
        lbl = self.step_labels.get(st)
        if lbl:
            lbl.config(text=icon, foreground={"✓": "#26a269", "✗": "#c01c28", "▶": "#1a5fb4",
                                              "⚠": "#b35c00"}.get(icon, ""))

    def progress(self, name: str, done: int, total: int):
        def show():
            if total:
                self.pb.stop()
                self.pb.config(mode="determinate", maximum=total, value=done)
                self.activity.config(text=T("setup.progress", name=name, done=i18n.num(done / 1e6, 0),
                                            total=i18n.num(total / 1e6, 0)))
        self.in_ui(show)

    def activity_text(self, text: str):
        def show():
            self.pb.config(mode="indeterminate")
            self.pb.start(12)
            self.activity.config(text=text)
        self.in_ui(show)

    def worker(self, steps: list[str], c: dict) -> dict:
        keep_awake(True)
        try:
            return Steps(self, c).run(steps)
        finally:
            keep_awake(False)

    def finished(self, result):
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        if isinstance(result, s.SetupError):
            self.show_error(result)
        elif isinstance(result, Exception):
            self.show_error(s.SetupError("generic", f"{type(result).__name__}: {result}"))
        elif result.get("restart"):
            self.restart_in_target()
        else:
            self.result = result
            self.done_page(result)

    def restart_in_target(self):
        """The program files are in the new folder: its own installer continues there."""
        py = s.pythonw(Path(sys.executable))
        s.write_state(self.target, pending=self.choices)
        self.log(T("setup.continue_in", path=self.target))
        try:
            subprocess.Popen([str(py), "-I", str(self.target / "wt_setup_ui.py"), "install", "--continue"],
                             cwd=str(self.target), creationflags=s.NO_WINDOW)
        except OSError as e:
            self.show_error(s.SetupError("generic", str(e)))
            return
        self.after(300, self.destroy)

    # ── Result ──

    def done_page(self, r: dict):
        f = self.page(T("setup.done_head"), "")
        report = install_report(r, self.a, self.source, self.target)
        for icon, text in report:
            self.line(f, icon, text)
        if r.get("old_venvs"):
            size = sum(s.tree_size(p) for p in r["old_venvs"])
            self.vars = {"delete_old": self.check(f, T("setup.done_delete_old", size=fmt_size(size)), True)}
        self.button(T("setup.btn_close"), self.close_done)
        self.button(T("setup.btn_start"), lambda: (self.start_workbench(), self.close_done()), accent=True)
        self.button(T("setup.btn_log"), lambda: open_path(self.target / s.LOG_FILE))
        for icon, text in report:
            self.log(f"{icon} {text}")

    def close_done(self):
        if getattr(self, "vars", {}).get("delete_old") and self.vars["delete_old"].get():
            for p in self.result.get("old_venvs", []):
                s.remove_tree(p)
        self.destroy()

    def start_workbench(self):
        os.environ["PATH"] = s.registry_path()
        bat = self.target / "Whisper_Tool.bat"
        if sys.platform == "win32" and bat.exists():
            subprocess.Popen(["cmd", "/c", "start", "", "/min", str(bat)], cwd=str(self.target),
                             creationflags=s.NO_WINDOW)
        else:
            subprocess.Popen([str(s.venv_python(self.target)), str(self.target / "whisper_tool.py")],
                             cwd=str(self.target))
        self.after(500, self.destroy)

    def show_error(self, e: s.SetupError):
        self.log(f"error: {e.key}: {e.detail}")
        f = self.page(T("setup.err_head"), "")
        key = f"setup.err_{e.key}" if i18n.has(f"setup.err_{e.key}") else "setup.err_generic"
        self.line(f, "✗", T(key, detail=e.detail, **e.kw))
        self.line(f, "•", T("setup.err_fix", log=self.target / s.LOG_FILE, url=s.PROJECT_URL))
        self.button(T("setup.btn_close"), self.destroy)
        self.button(T("setup.btn_retry"), self.start_analysis, accent=True)
        self.button(T("setup.btn_log"), lambda: open_path(self.target / s.LOG_FILE))
        backup = self.target / s.BACKUP_DIR / (self.a.get("target_version") or "")
        if self.a.get("target_version") and backup.is_dir():
            self.button(T("setup.btn_restore"), self.restore)

    def restore(self):
        version = self.a["target_version"]
        if s.restore_program(self.target, version, log=self.log):
            self.source = self.target
            self.mode = "inplace"
            self.a["target_version"] = ""
            self.start_analysis()


class Steps:
    """The steps of an installation, in the background thread; they talk to the window through w.log/w.in_ui."""

    def __init__(self, w: InstallWindow, c: dict):
        self.w, self.c, self.a = w, c, w.a
        self.target: Path = w.target
        self.py = s.venv_python(self.target)
        self.result: dict = {"system": [], "ini": {}, "warnings": [], "old_venvs": [], "before": {}, "after": {}}

    def run(self, steps: list[str]) -> dict:
        v = self.a["venv"]
        self.result["before"] = dict(v.installed) if v.exists and not v.problem else {}
        for st in steps:
            self.w.in_ui(self.w.step_state, st, "▶")
            self.w.activity_text(self.w.step_text(st))
            self.w.log(f"── {st}")
            try:
                warn = getattr(self, "step_" + st)()
            except s.SetupError:
                self.w.in_ui(self.w.step_state, st, "✗")
                raise
            self.w.in_ui(self.w.step_state, st, "⚠" if warn else "✓")
            if st == "copy":
                return {"restart": True}
        s.write_state(self.target, phase="complete", completed=time.strftime("%Y-%m-%d %H:%M"), pending=None)
        lock = self.target / s.LOCK_FILE
        if lock.exists():
            lock.unlink()
        return self.result

    def sh(self, cmd, key: str, timeout=None, fatal=True) -> int:
        rc = s.run(cmd, self.w.log, self.w.progress, timeout=timeout)
        if rc != 0 and fatal:
            raise s.SetupError(key, f"exit code {rc}")
        return rc

    def _lock(self):
        lock = self.target / s.LOCK_FILE
        try:
            pid = int(lock.read_text().strip())
        except (OSError, ValueError):
            pid = 0
        if pid and pid != os.getpid() and pid_alive(pid):
            raise s.SetupError("locked")
        lock.write_text(str(os.getpid()))

    # ── Program files ──

    def step_download(self):
        rel = self.a["release"]
        base = self.target / s.UPDATE_DIR if self.target.exists() else Path(tempfile.mkdtemp(prefix="wt-update-"))
        zpath = base / s.RELEASE_ASSET
        try:
            s.download(rel["zip"], zpath, self.w.progress)
            folder = s.extract_release(zpath, base / "files")
        except s.SetupError:
            raise
        except Exception as e:
            raise s.SetupError("download", f"{type(e).__name__}: {e}")
        if s.read_version(folder) != rel["version"]:
            raise s.SetupError("zip_broken", s.read_version(folder))
        self.w.source = folder
        self.w.log(f"downloaded {rel['version']} to {folder}")

    def step_copy(self):
        src = self.w.source
        if s.workbench_running():
            raise s.SetupError("running")
        self.target.mkdir(parents=True, exist_ok=True)
        self.w.in_ui(self.w.set_log_file, self.target / s.LOG_FILE)
        try:
            info = s.copy_program(src, self.target, log=self.w.log)
        except OSError as e:
            raise s.SetupError("copy", f"{e.filename}: {e.strerror}")
        s.unblock(self.target, info["copied"])
        s.write_state(self.target, downloaded_from=str(src))

    # ── Software on the PC ──

    def step_python(self):
        self.sh(s.winget_command("python"), "winget", fatal=False)
        pythons = s.find_pythons()
        best = s.choose_python(pythons)
        if best is None:
            raise s.SetupError("python_missing")
        self.a["python"] = best
        self.result["system"].append(best.text)
        s.write_state(self.target, installed_by_setup={**self.a["state"].get("installed_by_setup", {}), "python": True})

    def step_ffmpeg(self):
        self.sh(s.winget_command("ffmpeg"), "winget", fatal=False)
        path, ver, major = s.find_ffmpeg(self.target)
        if not ver:
            self.result["warnings"].append(T("setup.warn_ffmpeg"))
            return True
        self.result["system"].append(f"ffmpeg {ver}")
        self.result["ffmpeg_new"] = True
        st = s.read_state(self.target).get("installed_by_setup", {})
        s.write_state(self.target, installed_by_setup={**st, "ffmpeg": True}, ffmpeg=path)

    def step_vcredist(self):
        self.sh(s.winget_command("vcredist"), "winget", fatal=False)
        if not s.vcredist_installed():
            self.result["warnings"].append(T("setup.warn_vcredist"))
            return True
        self.result["system"].append("Microsoft Visual C++ Runtime")
        st = s.read_state(self.target).get("installed_by_setup", {})
        s.write_state(self.target, installed_by_setup={**st, "vcredist": True})

    # ── Python environment ──

    def step_venv(self):
        self._lock()
        vdir = self.target / "venv"
        if vdir.exists():
            old = self.target / f"venv-alt-{time.strftime('%Y%m%d-%H%M%S')}"
            try:
                os.rename(vdir, old)
            except OSError as e:
                raise s.SetupError("in_use", f"{vdir}: {e.strerror}")
            self.result["old_venvs"].append(old)
            self.result["before"] = {}
        py = self.a["python"]
        if py is None:
            raise s.SetupError("python_missing")
        self.sh([str(py.path), "-m", "venv", str(vdir)], "venv")
        if not self.py.is_file():
            raise s.SetupError("venv", str(self.py))

    def _started(self):
        py = self.a["python"]
        venv_info = self.a["venv"].info
        s.write_state(self.target, phase="started", started=time.strftime("%Y-%m-%d %H:%M"),
                      version=s.read_version(self.target), build=self.a["build"],
                      python=str(py.path if py else venv_info.path if venv_info else ""),
                      req_hash=s.requirements_hash(self.target), desktop=self.c.get("desktop", True),
                      startmenu=self.c.get("startmenu", True), model=self.c.get("model", True))

    def step_pip(self):
        self._lock()
        if not self.py.is_file():
            raise s.SetupError("venv", str(self.py))
        self._started()
        self.sh(s.pip(self.py, "install", "--upgrade", "pip"), "pip")

    def _pip_version(self) -> tuple:
        out = s._output([str(self.py), "-m", "pip", "--version"], timeout=60, env=s.clean_env()) or ""
        import re
        m = re.search(r"pip (\d+)\.(\d+)", out)
        return (int(m[1]), int(m[2])) if m else ()

    def step_torch(self):
        pins = s.read_pins(self.target / "constraints.txt")
        installed = s.inspect_venv(self.target).installed
        need = s.torch_needs_work(installed, pins, self.a["build"])
        if not need:
            self.w.log("torch fits")
            return
        if need == "rebuild":
            self.sh(s.pip(self.py, "uninstall", "-y", *s.TORCH_PKGS), "torch", fatal=False)
        self.sh(s.torch_command(self.py, self.a["build"], pins, self._pip_version()), "torch")

    def step_packages(self):
        self.sh(s.requirements_command(self.py, self.target, self._pip_version()), "packages")

    def step_dnd(self):
        pins = s.read_pins(self.target / "constraints.txt")
        if self.sh(s.dnd_command(self.py, pins), "dnd", fatal=False):
            self.result["warnings"].append(T("setup.warn_dnd"))
            return True

    def step_verify(self):
        out = s._output([str(self.py), "-I", "-c", s.VERIFY], timeout=600, env=s.clean_env()) or ""
        line = next((x for x in out.splitlines() if x.startswith("WT-VERIFY ")), "")
        self.w.log(line or out[-2000:])
        try:
            result = json.loads(line[len("WT-VERIFY "):])
        except ValueError:
            raise s.SetupError("torch_import", out[-500:])
        problem = s.verify_problem(result, self.a["build"])
        if problem:
            detail = result.get("torch_error") or next((str(v) for k, v in result.items()
                                                        if k in ("whisperx", "faster_whisper", "ctranslate2",
                                                                 "torchaudio") and v is not True), "")
            raise s.SetupError(problem, detail)
        self.result["gpu"] = result.get("gpu", "")
        self.result["after"] = s.inspect_venv(self.target).installed
        if s.run(s.pip(self.py, "check"), self.w.log) != 0:
            self.result["warnings"].append(T("setup.warn_pip_check"))
            return True

    def _task(self, *args) -> tuple[bool, object]:
        out = s._output([str(self.py), "-I", str(self.target / "wt_setup.py"), "task", *map(str, args)],
                        timeout=3600, env=s.clean_env()) or ""
        self.w.log(out.strip()[-2000:])
        line = next((x for x in out.splitlines() if x.startswith("WT-RESULT ")), "")
        if not line:
            return False, out.strip().splitlines()[-1] if out.strip() else ""
        return True, json.loads(line[len("WT-RESULT "):])

    def step_ini(self):
        build = self.a["build"]
        device = "cpu" if build == "cpu" else ""
        compute = "int8" if build == "cu126" else ""
        ok, changed = self._task("ini", self.target, device or "-", compute or "-")
        if not ok:
            self.result["warnings"].append(T("setup.warn_ini"))
            return True
        self.result["ini"] = changed or {}

    def step_extras(self):
        s.unblock(self.target, s.read_manifest(self.target))
        if sys.platform != "win32":
            return
        icon = s.pythonw(self.py)
        made = []
        if self.c.get("desktop", True):
            made.append(s.create_shortcut("Desktop", s.APP_NAME, self.target / "Whisper_Tool.bat", self.target,
                                          s.APP_NAME, icon, minimized=True))
        if self.c.get("startmenu", True):
            made.append(s.create_shortcut("Programs", s.APP_NAME, self.target / "Whisper_Tool.bat", self.target,
                                          s.APP_NAME, icon, minimized=True))
            made.append(s.create_shortcut("Programs", T("setup.shortcut_update", app=s.APP_NAME),
                                          self.target / "install.bat", self.target, s.APP_NAME, icon))
            try:
                s.register_app(self.target, s.read_version(self.target), s.tree_size(self.target / "venv"), icon)
            except OSError as e:
                self.w.log(f"apps entry: {e}")
        made = [m for m in made if m]
        old = s.read_state(self.target).get("shortcuts", [])
        s.write_state(self.target, shortcuts=sorted(set(old) | set(made)),
                      apps_entry=bool(self.c.get("startmenu", True)) or s.read_state(self.target).get("apps_entry"))
        if (self.c.get("desktop", True) or self.c.get("startmenu", True)) and not made:
            self.result["warnings"].append(T("setup.warn_shortcuts"))
            return True

    def step_model(self):
        ok, info = self._task("model", self.target)
        if not ok:
            self.result["warnings"].append(T("setup.warn_model", err=info))
            return True
        self.result["model_done"] = True

    def step_plugin(self):
        ok, info = self._task("plugin", self.target)
        if not ok:
            self.result["warnings"].append(T("setup.warn_plugin", err=info))
            return True
        self.result["plugin_done"] = True


def install_report(r: dict, a: dict, source: Path, target: Path) -> list[tuple[str, str]]:
    """Lines of the result page: what changed between the start and the end of this run."""
    out = []
    before, after = r.get("before", {}), r.get("after", {})
    changes = s.compare_installed(before, after) if after else []
    groups = s.by_kind(changes)
    if before:
        unchanged = len([k for k in after if s.norm_name(k) not in {s.norm_name(c.name) for c in changes}])
        for kind in ("update", "downgrade", "rebuild", "new", "removed"):
            items = groups.get(kind, [])
            if items:
                text = T(f"setup.done_{kind}", n=len(items)) + "\n" + "\n".join(
                    T("setup.ch_line", name=c.name, old=c.old or "–", new=c.new or "–") for c in items)
                out.append(("⚠" if kind == "downgrade" else "•", text))
        out.append(("•", T("setup.done_unchanged", n=unchanged)))
    elif after:
        out.append(("✓", T("setup.done_installed", n=len(after))))
    if r.get("gpu"):
        out.append(("✓", T("setup.done_gpu", name=r["gpu"])))
    if r.get("system"):
        out.append(("•", T("setup.done_system", items=", ".join(r["system"]))))
    for key, value in (r.get("ini") or {}).items():
        out.append(("•", T("setup.done_ini", key=key.split(".")[1], value=value)))
    for w in r.get("warnings", []):
        out.append(("⚠", w))
    if r.get("ffmpeg_new") and a.get("resolve"):
        out.append(("•", T("setup.done_hint_resolve")))
    if not r.get("model_done"):
        out.append(("•", T("setup.done_hint_model", model=a.get("model", ""))))
    if a.get("resolve") and not r.get("plugin_done") and not a.get("plugin"):
        out.append(("•", T("setup.done_hint_plugin")))
    downloaded = s.read_state(target).get("downloaded_from", "")
    if downloaded and os.path.normcase(downloaded) != os.path.normcase(str(target)) \
            and s.UPDATE_DIR not in Path(downloaded).parts and "wt-update-" not in downloaded:
        out.append(("•", T("setup.done_hint_download", path=downloaded)))
    return out


# ─── Uninstall ───────────────────────────────────────────────────────────────

class UninstallWindow(Wizard):
    ORDER = ("temp", "plugin", "cache", "models", "resolve", "venv", "program")

    def __init__(self, folder: Path):
        super().__init__(T("setup.title_uninstall", app=s.APP_NAME))
        self.folder = folder
        self.start_analysis()

    def start_analysis(self):
        self.checking(T("setup.un_checking"))
        self.background(lambda: (s.uninstall_plan(self.folder), s.workbench_running()), self.analysed)

    def analysed(self, result):
        if isinstance(result, Exception):
            self.show_failed(str(result))
            return
        self.plan, self.running = result
        g = self.plan["groups"]
        f = self.page(T("setup.un_head"), T("setup.un_folder", path=self.folder))
        self.vars = {}
        if self.running:
            b = ttk.Button(f, text=T("setup.btn_check_again"), command=self.start_analysis)
            self.line(f, "✗", T("setup.block_running"), widget=b)
        self.section(f, T("setup.un_section_remove"))
        for key in ("venv", "program", "models", "resolve", "plugin", "cache"):
            grp = g[key]
            if key == "program" and self.plan["git"]:
                self.line(f, "•", T("setup.un_g_program_git"))
                continue
            if not grp.paths:
                continue
            self.vars[key] = self.check(f, T(f"setup.un_g_{key}", size=fmt_size(grp.size)), grp.default)
        self.line(f, "•", T("setup.un_temp", size=fmt_size(g["temp"].size)))
        self.section(f, T("setup.un_section_keep"))
        if self.plan["keep"]:
            self.line(f, "✓", T("setup.un_keep", files=", ".join(p.name for p in self.plan["keep"])))
        foreign = self.plan["foreign"]
        if foreign:
            self.line(f, "•", Tn("setup.un_foreign", len(foreign)))
            shown = "\n".join(self._rel(p) for p in foreign[:SHOWN_FOREIGN])
            if len(foreign) > SHOWN_FOREIGN:
                shown += "\n" + T("setup.un_more", n=len(foreign) - SHOWN_FOREIGN, file=s.REPORT_FILE)
            self.line(f, "", shown, indent=22)
        self.button(T("setup.btn_cancel"), self.destroy)
        b = self.button(T("setup.btn_remove"), self.remove, accent=True)
        if self.running:
            b.configure(state="disabled", background="#9db8e0")

    def _rel(self, p: Path) -> str:
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        rel = str(p.relative_to(self.folder))
        return f"{rel} ({fmt_size(size)})" if size >= 1e6 else rel

    def remove(self):
        chosen = ["temp"] + [k for k in self.ORDER if k in self.vars and self.vars[k].get()]
        f = self.page(T("setup.un_run_head"), "", scroll=False)
        pb = ttk.Progressbar(f, mode="indeterminate")
        pb.pack(fill="x", pady=20)
        pb.start(12)
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.background(lambda: self.worker(chosen), self.removed)

    def worker(self, chosen: list[str]) -> dict:
        g, failed, removed, freed = self.plan["groups"], [], [], 0
        state = self.plan["state"]
        for key in [k for k in self.ORDER if k in chosen]:
            grp = g[key]
            paths = list(grp.paths)
            if key == "venv":
                paths = []
                for p in grp.paths:
                    moved = p.with_name(p.name + ".deleting")
                    try:
                        os.rename(p, moved)
                        paths.append(moved)
                    except OSError as e:
                        failed.append((str(p), e.strerror or str(e)))
            if key == "program":
                kit = {k.lower() for k in KIT} | {"lang"}
                keep_kit = bool(failed)
                paths = [p for p in paths if not keep_kit or
                         p.relative_to(self.folder).parts[0].lower() not in kit]
            before = len(failed)
            for p in paths:
                self.log(f"remove {p}")
                failed += s.remove_tree(p)
            if key == "program":
                for lnk in state.get("shortcuts", []):
                    failed += s.remove_tree(Path(lnk))
                if not failed:
                    s.unregister_app()
                    failed += s.remove_tree(self.folder / s.STATE_FILE)
                    self._remove_empty_dirs()
            if len(failed) == before:
                removed.append(key)
            freed += grp.size
        return {"removed": removed, "failed": failed, "freed": freed, "state": state}

    def _remove_empty_dirs(self):
        for root, dirs, files in sorted(os.walk(self.folder), key=lambda x: -len(x[0])):
            p = Path(root)
            if p != self.folder and not os.listdir(p):
                try:
                    p.rmdir()
                except OSError:
                    pass

    def removed(self, r):
        if isinstance(r, Exception):
            self.show_failed(str(r))
            return
        text = uninstall_report(r, self.plan, self.folder)
        try:
            (self.folder / s.REPORT_FILE).write_text(text + "\n", encoding="utf-8")
            text += "\n\n" + T("setup.un_report_saved", path=self.folder / s.REPORT_FILE)
        except OSError:
            pass
        f = self.page(T("setup.un_done_head") if not r["failed"] else T("setup.un_done_partly"), "", scroll=False)
        t = tk.Text(f, wrap="word", relief="flat", background=self.bg, borderwidth=0,
                    font=tkfont.nametofont("TkDefaultFont"))
        t.insert("1.0", text)
        t.configure(state="disabled")
        t.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.button(T("setup.btn_close"), self.destroy, accent=True)
        self.button(T("setup.btn_open_folder"), lambda: open_path(self.folder))

    def show_failed(self, detail: str):
        f = self.page(T("setup.un_done_partly"), "")
        self.line(f, "✗", detail)
        self.button(T("setup.btn_close"), self.destroy)


def uninstall_report(r: dict, plan: dict, folder: Path) -> str:
    lines = []
    names = [T(f"setup.un_name_{k}") for k in r["removed"]]
    lines.append(T("setup.un_done_removed", size=fmt_size(r["freed"]), items=", ".join(names)))
    left = s.system_leftovers(folder, r["state"])
    if left:
        lines += ["", T("setup.un_done_left")]
        for item in left:
            by = T("setup.un_by_setup" if item.get("by_setup") else "setup.un_by_before")
            lines.append("- " + T(f"setup.un_left_{item['key']}", path=item.get("path", ""), by=by,
                                  size=fmt_size(item.get("size", 0))))
    kept = [p for p in plan["keep"] if p.exists()]
    foreign = [p for p in plan["foreign"] if p.exists()]
    if kept or foreign:
        lines += ["", T("setup.un_done_folder", path=folder)]
        if kept:
            lines.append("- " + T("setup.un_keep", files=", ".join(p.name for p in kept)))
        if foreign:
            lines.append("- " + Tn("setup.un_foreign", len(foreign)))
            lines += [f"    {p.relative_to(folder)}" for p in foreign]
    if r["failed"]:
        lines += ["", Tn("setup.un_done_failed", len(r["failed"]))]
        lines += [f"- {p} – {err}" for p, err in r["failed"]]
        lines.append(T("setup.un_failed_fix"))
    return "\n".join(lines)


# ─── Start ───────────────────────────────────────────────────────────────────

def _dpi_aware():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def main(argv: list[str]) -> int:
    what = argv[0] if argv else "install"
    folder = HERE
    ini = folder / s.INI
    i18n.load(i18n.ini_language(ini if ini.exists() else folder / s.EXAMPLE_INI))
    if tk is None:
        print(T("setup.no_tkinter"))
        return 1
    _dpi_aware()
    if what == "uninstall":
        UninstallWindow(folder).mainloop()
    else:
        InstallWindow(folder, resume="--continue" in argv).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
