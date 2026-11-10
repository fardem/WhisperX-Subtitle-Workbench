"""
Installation, repair, update and removal of the Workbench: logic without user interface (wt_setup_ui.py).
Standard library only: it runs with the system Python, never from venv\\, so that it can recreate the venv.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

APP_NAME      = "WhisperX Subtitle Workbench"
APP_DIR       = "WhisperX-Subtitle-Workbench"
STATE_FILE    = "wt_install.json"
MANIFEST      = "wt_files.txt"
LOG_FILE      = "install.log"
LOCK_FILE     = "install.lock"
REPORT_FILE   = "uninstall_report.txt"
UPDATE_CHECK  = "update_check.json"
UPDATE_DIR    = "update"
BACKUP_DIR    = "update-backup"
INI           = "whisper_tool.ini"
EXAMPLE_INI   = "whisper_tool.example.ini"
KEEP_FILES    = (INI, INI + ".bak")
TEMP_FILES    = (LOG_FILE, LOCK_FILE, UPDATE_CHECK, "whisper_tool_error.log", "whisper_tool_shutdown.log",
                 INI + ".tmp")
TEMP_DIRS     = ("temp", UPDATE_DIR, ".pytest_cache")

PY_MIN, PY_MAX = (3, 10), (3, 13)              # WhisperX 3.8 needs Python below 3.14
DRIVER_MIN    = 570                            # CUDA 12.8 wheels
CC_CU128      = (7, 5)                         # the cu128 wheels of torch 2.8 start at Turing (sm_75)
CC_CUDA       = (5, 0)                         # the cu126 wheels start at Maxwell (sm_50)
FFMPEG_MAJORS = (6, 9)
TORCH_PKGS    = ("torch", "torchaudio", "torchvision")
TORCH_INDEX   = "https://download.pytorch.org/whl/{build}"
PIP_REPORT    = (22, 2)                        # pip install --dry-run --report
PIP_RAW       = (25, 0)                        # --progress-bar raw
DISK_GB       = 15                             # venv, pip cache and Whisper model; an estimate, not measured
LONG_PATH     = 110                            # + the longest file in the venv (142 characters) stays below 260
DND_PKG       = "tkinterdnd2"
IPC_ADDR      = ("127.0.0.1", 47653)           # the same as IPC_ADDR in whisper_tool.py
GITHUB_REPO   = "fardem/WhisperX-Subtitle-Workbench"
RELEASE_API   = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASE_ASSET = "WhisperX-Subtitle-Workbench.zip"
PROJECT_URL   = f"https://github.com/{GITHUB_REPO}"
PYTHON_URL    = "https://www.python.org/downloads/windows/"
DRIVER_URL    = "https://www.nvidia.com/Download/index.aspx"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\WhisperX-Subtitle-Workbench"
WINGET_IDS    = {"python": "Python.Python.3.13", "ffmpeg": "Gyan.FFmpeg", "vcredist": "Microsoft.VCRedist.2015+.x64"}
NO_WINDOW     = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WINDOWS       = os.name == "nt"


class SetupError(RuntimeError):
    """A step failed; key names the text under setup.err_*, detail is the technical part for the log."""
    def __init__(self, key: str, detail: str = "", **kw):
        super().__init__(f"{key}: {detail}")
        self.key, self.detail, self.kw = key, detail, kw


# ─── Versions ────────────────────────────────────────────────────────────────

_VERSION = re.compile(r"""^\s*v?(?:(\d+)!)?(\d+(?:\.\d+)*)
    (?:[-_.]?(a|alpha|b|beta|c|rc|pre|preview)[-_.]?(\d*))?
    (?:-(\d+)|[-_.]?(post|rev|r)[-_.]?(\d*))?
    (?:[-_.]?(dev)[-_.]?(\d*))?
    (?:\+([a-z0-9]+(?:[-_.][a-z0-9]+)*))?\s*$""", re.I | re.X)
_PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2, "preview": 2}


def version_key(v: str) -> tuple:
    """Version order of pip without the local part: 1.0.dev0 < 1.0a1 < 1.0 < 1.0.post1; 2.8.0+cu128 == 2.8.0."""
    m = _VERSION.match(v or "")
    if not m:
        return (-1,)
    release = [int(x) for x in m[2].split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    has_post = m[5] is not None or m[6] is not None
    if m[3]:
        pre = (1, _PRE_RANK[m[3].lower()], int(m[4] or 0))
    else:
        pre = (0,) if m[8] and not has_post else (2,)
    post = (1, int(m[5] or m[7] or 0)) if has_post else (0,)
    dev = (1, int(m[9] or 0)) if m[8] else (2,)
    return (int(m[1] or 0), tuple(release), pre, post, dev)


def local_label(v: str) -> str:
    return v.split("+", 1)[1].lower() if "+" in (v or "") else ""


def newer(a: str, b: str) -> bool:
    return version_key(a) > version_key(b)


def norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(path) -> dict[str, str]:
    """name == version lines of requirements.txt or constraints.txt → {normalized name: version}."""
    out = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.split("#")[0].strip()
        if "==" in line:
            name, ver = line.split("==", 1)
            out[norm_name(name.strip())] = ver.strip()
    return out


def read_version(folder) -> str:
    """VERSION from wt_version.py without importing it."""
    try:
        text = (Path(folder) / "wt_version.py").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
    return m[1] if m else ""


# ─── Packages: what changes ─────────────────────────────────────────────────

@dataclass
class Change:
    name: str
    old: str           # "" = not installed
    new: str           # "" = removed or stays (foreign)
    kind: str          # update, downgrade, rebuild, new, removed, foreign


def classify_change(old: str, new: str) -> str:
    if not old:
        return "new"
    if not new:
        return "removed"
    if version_key(old) == version_key(new):
        return "rebuild" if local_label(old) != local_label(new) else "same"
    return "update" if newer(new, old) else "downgrade"


def compare_installed(before: dict, after: dict) -> list[Change]:
    """Report after a run: every package whose version or build differs between before and after."""
    b = {norm_name(k): (k, v) for k, v in before.items()}
    a = {norm_name(k): (k, v) for k, v in after.items()}
    out = []
    for key in sorted(set(a) | set(b)):
        name = (a.get(key) or b.get(key))[0]
        old, new = b.get(key, ("", ""))[1], a.get(key, ("", ""))[1]
        kind = classify_change(old, new)
        if kind != "same":
            out.append(Change(name, old, new, kind))
    return out


def plan_changes(installed: dict, pins: dict, build: str, report: dict | None = None) -> list[Change]:
    """
    What the installation would change: pins of constraints.txt (torch with the build label), refined by the
    versions of pip's dry-run report. Packages that are not in the pins stay ("foreign").
    """
    inst = {norm_name(k): (k, v) for k, v in installed.items()}
    target = {}
    for key, ver in pins.items():
        target[key] = f"{ver}+{build}" if key in TORCH_PKGS else ver
    for name, ver in (report or {}).items():
        key = norm_name(name)
        if key not in TORCH_PKGS:
            target[key] = ver
    out = []
    for key in sorted(target):
        old = inst.get(key, ("", ""))[1]
        kind = classify_change(old, target[key])
        if key in TORCH_PKGS and kind == "same" and local_label(old) != build:
            kind = "rebuild"
        if kind != "same":
            out.append(Change(inst.get(key, (key, ""))[0], old, target[key], kind))
    for key, (name, ver) in sorted(inst.items()):
        if key not in target and key not in ("pip", "wheel"):
            out.append(Change(name, ver, "", "foreign"))
    return out


def by_kind(changes: list[Change]) -> dict[str, list[Change]]:
    out: dict[str, list[Change]] = {}
    for c in changes:
        out.setdefault(c.kind, []).append(c)
    return out


def torch_needs_work(installed: dict, pins: dict, build: str) -> str:
    """"" = torch, torchaudio, torchvision fit; "install" = missing or another version; "rebuild" = other build."""
    inst = {norm_name(k): v for k, v in installed.items()}
    result = ""
    for key in TORCH_PKGS:
        have, want = inst.get(key, ""), pins.get(key, "")
        if not have or version_key(have) != version_key(want):
            return "install"
        if local_label(have) != build:
            result = "rebuild"
    return result


def parse_pip_report(text: str) -> dict[str, str]:
    data = json.loads(text)
    return {i["metadata"]["name"]: i["metadata"]["version"] for i in data.get("install", [])}


# ─── Graphics card ──────────────────────────────────────────────────────────

@dataclass
class Gpu:
    name: str
    driver: str
    cc: tuple | None


# Compute capability by name, for drivers whose nvidia-smi does not know the field compute_cap.
NAME_CC = [(r"RTX\s*50\d\d", (12, 0)), (r"RTX\s*40\d\d", (8, 9)), (r"RTX\s*30\d\d", (8, 6)),
           (r"RTX\s*20\d\d|TITAN RTX|GTX\s*16\d\d|Quadro RTX|\bT\d{3,4}\b", (7, 5)), (r"TITAN V\b", (7, 0)),
           (r"GTX\s*10\d\d|TITAN Xp?\b|Quadro P", (6, 1)), (r"GTX\s*9\d\d|Quadro M", (5, 2)),
           (r"GTX\s*[67]\d\d|Quadro K", (3, 5))]


def cc_from_name(name: str) -> tuple | None:
    for pattern, cc in NAME_CC:
        if re.search(pattern, name, re.I):
            return cc
    return None


def parse_nvidia_smi(text: str) -> list[Gpu]:
    """Lines "name, driver[, compute_cap]" of nvidia-smi --query-gpu … --format=csv,noheader."""
    out = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[0] or "NVIDIA-SMI" in line or "failed" in line.lower():
            continue
        cc = None
        if len(parts) > 2 and re.fullmatch(r"\d+\.\d+", parts[2]):
            cc = tuple(int(x) for x in parts[2].split("."))
        out.append(Gpu(parts[0], parts[1], cc or cc_from_name(parts[0])))
    return out


def driver_major(driver: str) -> int | None:
    m = re.match(r"(\d+)", driver or "")
    return int(m[1]) if m else None


def choose_build(gpus: list[Gpu]) -> tuple[str, Gpu | None]:
    """→ (cu128 | cu126 | cpu, the card that decides). The card with the highest compute capability counts."""
    if not gpus:
        return "cpu", None
    best = max(gpus, key=lambda g: g.cc or (99, 0))
    cc = best.cc
    if cc is None or cc >= CC_CU128:
        return "cu128", best
    return ("cu126" if cc >= CC_CUDA else "cpu"), best


def nvidia_smi_path() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    for base in (os.environ.get("SystemRoot", r"C:\Windows") + r"\System32",
                 os.environ.get("ProgramFiles", r"C:\Program Files") + r"\NVIDIA Corporation\NVSMI"):
        p = Path(base) / "nvidia-smi.exe"
        if p.is_file():
            return str(p)
    return None


def find_gpus() -> tuple[list[Gpu], bool]:
    """→ (NVIDIA cards with driver, an NVIDIA card without working driver was found)."""
    smi = nvidia_smi_path()
    if smi:
        for fields in ("name,driver_version,compute_cap", "name,driver_version"):
            out = _output([smi, f"--query-gpu={fields}", "--format=csv,noheader"], timeout=30)
            gpus = parse_nvidia_smi(out or "")
            if gpus:
                return gpus, False
    names = video_controllers()
    return [], any("nvidia" in n.lower() for n in names)


def video_controllers() -> list[str]:
    if not WINDOWS:
        return []
    out = _output(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                   "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"], timeout=30)
    return [line.strip() for line in (out or "").splitlines() if line.strip()]


# ─── Python ─────────────────────────────────────────────────────────────────

@dataclass
class PythonInfo:
    path: Path
    version: tuple = ()
    bits: int = 0
    tkinter: bool = False
    venv: bool = False
    freethreaded: bool = False
    base: str = ""

    @property
    def text(self) -> str:
        return "Python " + ".".join(map(str, self.version))

    @property
    def store(self) -> bool:
        return "\\windowsapps\\" in str(self.path).lower()

    @property
    def conda(self) -> bool:
        return bool(self.base) and (Path(self.base) / "conda-meta").is_dir()

    def problem(self) -> str:
        """"" = usable for the venv, otherwise the reason (text setup.py_<reason>)."""
        if self.store:
            return "store"
        if self.conda:
            return "conda"
        if self.bits and self.bits != 64:
            return "bits"
        if self.freethreaded:
            return "freethreaded"
        if self.version[:2] > PY_MAX:
            return "new"
        if self.version[:2] < PY_MIN:
            return "old"
        if not self.tkinter:
            return "tkinter"
        if not self.venv:
            return "venv"
        return ""


PROBE = ("import sys, struct, sysconfig, json, importlib.util as u; print(json.dumps({'v': list(sys.version_info[:3]),"
         " 'bits': struct.calcsize('P') * 8, 'tk': u.find_spec('tkinter') is not None, 'venv': u.find_spec('venv')"
         " is not None and u.find_spec('ensurepip') is not None, 'ft': bool(sysconfig.get_config_var('Py_GIL_DISABLED')),"
         " 'base': sys.base_prefix}))")


def probe_python(path) -> PythonInfo | None:
    out = _output([str(path), "-I", "-c", PROBE], timeout=30)
    try:
        d = json.loads((out or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return PythonInfo(Path(path), tuple(d["v"]), d["bits"], d["tk"], d["venv"], d["ft"], d["base"])


def registry_pythons() -> list[Path]:
    """Installed Pythons from the registry (Software\\Python), where python.org and the install manager list them."""
    if not WINDOWS:
        return []
    import winreg
    found = []
    for hive, flags in ((winreg.HKEY_CURRENT_USER, 0), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY)):
        try:
            root = winreg.OpenKey(hive, r"Software\Python", 0, winreg.KEY_READ | flags)
        except OSError:
            continue
        for company in _subkeys(root):
            ckey = winreg.OpenKey(root, company)
            for tag in _subkeys(ckey):
                try:
                    ip = winreg.OpenKey(ckey, tag + r"\InstallPath")
                except OSError:
                    continue
                exe = _reg_value(ip, "ExecutablePath")
                if not exe:
                    base = _reg_value(ip, "")
                    exe = str(Path(base) / "python.exe") if base else ""
                if exe and Path(exe).is_file():
                    found.append(Path(exe))
    return found


def _subkeys(key) -> list[str]:
    import winreg
    out, i = [], 0
    while True:
        try:
            out.append(winreg.EnumKey(key, i))
        except OSError:
            return out
        i += 1


def _reg_value(key, name: str) -> str:
    import winreg
    try:
        return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def standard_pythons(env=os.environ) -> list[Path]:
    out = []
    for base in (Path(env.get("LOCALAPPDATA", "")) / "Programs" / "Python", Path(env.get("ProgramFiles", ""))):
        if str(base) in ("", ".") or not base.is_dir():
            continue
        out += sorted(base.glob("Python3*/python.exe"))
    return out


def find_pythons() -> list[PythonInfo]:
    seen, out = set(), []
    running = Path(sys.executable) if sys.executable else None
    if running and running.name.lower() == "pythonw.exe":
        running = running.with_name("python.exe")
    paths = registry_pythons() + standard_pythons() + ([running] if running else [])
    for p in paths:
        key = os.path.normcase(str(p))
        if key in seen or "\\windowsapps\\python" in key and "pythonsoftwarefoundation" not in key:
            continue
        seen.add(key)
        info = probe_python(p)
        if info:
            out.append(info)
    return out


def choose_python(cands: list[PythonInfo]) -> PythonInfo | None:
    """3.13 first, then 3.12, 3.11, 3.10; the newest patch release of that line."""
    usable = [c for c in cands if not c.problem()]
    if not usable:
        return None
    return max(usable, key=lambda c: (c.version[:2] == (3, 13), c.version))


def pythonw(path: Path) -> Path:
    w = path.with_name("pythonw.exe")
    return w if path.name.lower() == "python.exe" and w.exists() else path


# ─── Python environment (venv) ─────────────────────────────────────────────

def venv_python(folder) -> Path:
    v = Path(folder) / "venv"
    return v / "Scripts" / "python.exe" if WINDOWS else v / "bin" / "python"


def read_pyvenv_cfg(venv_dir) -> dict:
    out = {}
    try:
        for line in (Path(venv_dir) / "pyvenv.cfg").read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip().lower()] = v.strip()
    except OSError:
        pass
    return out


INVENTORY = ("import json, importlib.metadata as m; print(json.dumps({d.metadata.get('Name'): d.version"
             " for d in m.distributions() if d.metadata.get('Name')}))")


@dataclass
class VenvState:
    exists: bool = False
    problem: str = ""                   # no_python, home_missing, broken, or a PythonInfo problem
    info: PythonInfo | None = None
    installed: dict = field(default_factory=dict)

    @property
    def pip(self) -> tuple:
        v = next((v for k, v in self.installed.items() if norm_name(k) == "pip"), "")
        return tuple(int(x) for x in re.findall(r"\d+", v)[:2]) if v else ()


def inspect_venv(folder) -> VenvState:
    vdir = Path(folder) / "venv"
    if not vdir.is_dir():
        return VenvState()
    py = venv_python(folder)
    if not py.is_file():
        return VenvState(True, "no_python")
    home = read_pyvenv_cfg(vdir).get("home", "")
    if home and not Path(home).is_dir():
        return VenvState(True, "home_missing")
    info = probe_python(py)
    if info is None:
        return VenvState(True, "broken")
    problem = info.problem()
    if problem == "venv":                   # ensurepip is not needed inside the venv
        problem = ""
    out = _output([str(py), "-I", "-c", INVENTORY], timeout=120)
    try:
        installed = json.loads((out or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return VenvState(True, "broken", info)
    return VenvState(True, problem, info, installed)


# ─── State file and manifest ───────────────────────────────────────────────

def read_state(folder) -> dict:
    try:
        return json.loads((Path(folder) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(folder, **values) -> dict:
    state = read_state(folder)
    state.update(values)
    path = Path(folder) / STATE_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return state


def requirements_hash(folder) -> str:
    h = hashlib.sha256()
    for name in ("requirements.txt", "constraints.txt"):
        try:
            h.update((Path(folder) / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:16]


def read_manifest(folder) -> list[str]:
    """Program files of a version (wt_files.txt, tools/manifest.py writes it), relative with "/"."""
    try:
        lines = (Path(folder) / MANIFEST).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def is_installation(folder) -> bool:
    folder = Path(folder)
    return (folder / "venv").is_dir() or (folder / INI).is_file() or (folder / STATE_FILE).is_file()


# ─── Scenario of an existing installation ─────────────────────────────────

@dataclass
class Scenario:
    code: str                           # new, parent, broken, incomplete, changes, ok
    changes: list = field(default_factory=list)
    torch: str = ""                     # "", install, rebuild
    report: bool = False                # changes come from pip's dry-run report


def classify(venv: VenvState, state: dict, pins: dict, build: str, req_hash: str, parent_venv: bool,
             report: dict | None = None) -> Scenario:
    if not venv.exists:
        return Scenario("parent" if parent_venv else "new")
    if venv.problem:
        return Scenario("broken")
    changes = plan_changes(venv.installed, pins, build, report)
    torch = torch_needs_work(venv.installed, pins, build)
    if state.get("phase") == "started":
        return Scenario("incomplete", changes, torch, report is not None)
    real = [c for c in changes if c.kind != "foreign"]
    if real or torch:
        return Scenario("changes", changes, torch, report is not None)
    return Scenario("ok", changes, "", report is not None)


# ─── Checks of the PC ──────────────────────────────────────────────────────

def default_target(env=os.environ) -> Path:
    drive = env.get("SystemDrive", "C:")
    return Path(drive + "\\") / APP_DIR if WINDOWS else Path.home() / APP_DIR


def _under(path: Path, base: str) -> bool:
    if not base:
        return False
    try:
        a, b = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(base))
    except (OSError, ValueError):
        return False
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def path_warnings(path, env=os.environ, long_paths: bool = False) -> list[str]:
    """Problems of an installation folder → keys setup.path_<code>."""
    path = Path(path)
    s = str(path)
    out = []
    if not s.isascii():
        out.append("non_ascii")
    if s.startswith("\\\\"):
        out.append("unc")
    if any(_under(path, env.get(v, "")) for v in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")):
        out.append("onedrive")
    if any(_under(path, env.get(v, "")) for v in ("TEMP", "TMP")):
        out.append("temp")
    if any(_under(path, env.get(v, "")) for v in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")):
        out.append("program_files")
    if len(s) > LONG_PATH and not long_paths:
        out.append("long")
    return out


def long_paths_enabled() -> bool:
    if not WINDOWS:
        return True
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem")
        return bool(winreg.QueryValueEx(key, "LongPathsEnabled")[0])
    except OSError:
        return False


def writable(folder) -> bool:
    """Create the folder like the installation would (C:\\ allows new folders, not new files) and write a file."""
    folder = Path(folder)
    created = []
    try:
        for p in reversed([folder, *folder.parents]):
            if not p.exists():
                p.mkdir()
                created.append(p)
        test = folder / f".wt-write-test-{os.getpid()}"
        test.write_text("x", encoding="utf-8")
        test.unlink()
        return True
    except OSError:
        return False
    finally:
        for p in reversed(created):
            try:
                p.rmdir()
            except OSError:
                pass


def free_gb(folder) -> float:
    folder = Path(folder)
    probe = folder if folder.exists() else next((p for p in folder.parents if p.exists()), folder.anchor or "/")
    try:
        return shutil.disk_usage(probe).free / 1e9
    except OSError:
        return 0.0


def is_admin() -> bool:
    if not WINDOWS:
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def is_arm() -> bool:
    arch = (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or "").upper()
    return arch == "ARM64"


def workbench_running(timeout: float = 0.5) -> bool:
    """A Workbench window listens on IPC_ADDR (whisper_tool.py), whatever its folder is."""
    try:
        with socket.create_connection(IPC_ADDR, timeout=timeout):
            return True
    except OSError:
        return False


def internet(timeout: float = 8) -> bool:
    for url in ("https://pypi.org/simple/pip/", "https://download.pytorch.org/whl/"):
        try:
            _get(url, timeout=timeout)
        except Exception:
            return False
    return True


def vcredist_installed() -> bool:
    if not WINDOWS:
        return True
    sysdir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return all((sysdir / n).is_file() for n in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"))


def winget() -> str | None:
    return shutil.which("winget")


def winget_command(what: str) -> list[str]:
    cmd = ["winget", "install", "-e", "--id", WINGET_IDS[what], "--accept-package-agreements",
           "--accept-source-agreements", "--disable-interactivity"]
    return cmd + (["--scope", "user"] if what == "python" else [])


def registry_path() -> str:
    """PATH as new programs get it: a program installed by winget is in it, the running process's PATH is old."""
    if not WINDOWS:
        return os.environ.get("PATH", "")
    import winreg
    parts = []
    for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            parts.append(os.path.expandvars(_reg_value(winreg.OpenKey(hive, sub), "Path")))
        except OSError:
            pass
    return ";".join(p for p in parts if p) or os.environ.get("PATH", "")


def resolve_installed(env=os.environ) -> bool:
    for base in (env.get("ProgramFiles", ""), env.get("APPDATA", "")):
        if base and (Path(base) / "Blackmagic Design" / "DaVinci Resolve").exists():
            return True
    return False


# ─── ffmpeg ────────────────────────────────────────────────────────────────

def parse_ffmpeg_version(text: str) -> tuple[str, int | None]:
    """First line of ffmpeg -version → (version, major); a git build (N-…, 2024-…-git) has no major."""
    m = re.search(r"ffmpeg version (\S+)", text or "")
    if not m:
        return "", None
    ver = m[1]
    major = re.match(r"n?(\d+)\.", ver)
    return ver, int(major[1]) if major and not re.match(r"\d{4}-", ver) else None


def ffmpeg_ok(version: str, major: int | None) -> bool:
    return bool(version) and (major is None or FFMPEG_MAJORS[0] <= major <= FFMPEG_MAJORS[1])


def ini_value(folder, section: str, key: str, default: str = "") -> str:
    import configparser
    cp = configparser.ConfigParser(delimiters=("=",), interpolation=None, strict=False)
    try:
        cp.read(Path(folder) / INI, encoding="utf-8-sig")
        return cp.get(section, key, fallback=default).strip() or default
    except (configparser.Error, OSError, UnicodeDecodeError):
        return default


def ffmpeg_candidates(folder, env=os.environ) -> list[str]:
    out = []
    configured = ini_value(folder, "System", "ffmpeg_path", "ffmpeg")
    if configured and configured != "ffmpeg":
        out.append(configured)
    for path in (shutil.which("ffmpeg"), shutil.which("ffmpeg", path=registry_path())):
        if path:
            out.append(path)
    local = Path(env.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet"
    if env.get("LOCALAPPDATA"):
        out.append(str(local / "Links" / "ffmpeg.exe"))
        out += [str(p) for p in sorted((local / "Packages").glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"))]
    return list(dict.fromkeys(out))


def find_ffmpeg(folder) -> tuple[str, str, int | None]:
    """→ (path, version, major) of the first ffmpeg that answers; ("", "", None) if there is none."""
    for cand in ffmpeg_candidates(folder):
        if not Path(cand).is_file() and not shutil.which(cand):
            continue
        out = _output([cand, "-version"], timeout=20)
        ver, major = parse_ffmpeg_version((out or "").splitlines()[0] if out else "")
        if ver:
            return cand, ver, major
    return "", "", None


# ─── Commands ──────────────────────────────────────────────────────────────

def clean_env(base=None) -> dict:
    """pip only inside a venv, no user site, no PYTHONPATH of other programs."""
    env = dict(os.environ if base is None else base)
    for k in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PIP_USER", "PIP_TARGET", "PIP_PREFIX"):
        env.pop(k, None)
    env.update(PIP_REQUIRE_VIRTUALENV="1", PYTHONNOUSERSITE="1", PIP_DISABLE_PIP_VERSION_CHECK="1",
               PIP_NO_INPUT="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return env


def pip(py, *args) -> list[str]:
    return [str(py), "-m", "pip", *args]


def progress_args(pip_version: tuple) -> list[str]:
    return ["--progress-bar", "raw"] if pip_version >= PIP_RAW else ["--progress-bar", "off"]


def torch_command(py, build: str, pins: dict, pip_version: tuple = ()) -> list[str]:
    """Step 4 of the manual installation: torch first, from the PyTorch index; PyPI has only CPU builds."""
    pkgs = [f"{p}=={pins[p]}" for p in TORCH_PKGS]
    return pip(py, "install", *pkgs, "--index-url", TORCH_INDEX.format(build=build), *progress_args(pip_version))


def requirements_command(py, folder, pip_version: tuple = ()) -> list[str]:
    folder = Path(folder)
    return pip(py, "install", "-r", str(folder / "requirements.txt"), "-c", str(folder / "constraints.txt"),
               *progress_args(pip_version))


def dry_run_command(py, folder) -> list[str]:
    folder = Path(folder)
    return pip(py, "install", "--dry-run", "--quiet", "--report", "-", "-r", str(folder / "requirements.txt"),
               "-c", str(folder / "constraints.txt"))


def dnd_command(py, pins: dict) -> list[str]:
    return pip(py, "install", "--no-deps", f"{DND_PKG}=={pins[DND_PKG]}")


def _output(cmd, timeout=60, env=None) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, creationflags=NO_WINDOW,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return r.stdout.decode("utf-8", "replace")


PROGRESS = re.compile(r"^Progress (\d+) of (\d+)")
DOWNLOADING = re.compile(r"^\s*Downloading (\S+)")


def run(cmd, log, progress=None, env=None, cwd=None, timeout=None) -> int:
    """Run a command; each line goes to log(line), "Progress x of y" lines of pip to progress(name, x, y)."""
    log("$ " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    proc = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env or clean_env(), cwd=cwd, creationflags=NO_WINDOW)
    name, start = "", time.monotonic()
    for raw in iter(proc.stdout.readline, b""):
        line = raw.decode("utf-8", "replace").rstrip()
        m = PROGRESS.match(line)
        if m:
            if progress:
                progress(name, int(m[1]), int(m[2]))
            continue
        d = DOWNLOADING.match(line)
        if d:
            name = d[1].split("/")[-1]
        log(line)
        if timeout and time.monotonic() - start > timeout:
            proc.kill()
    return proc.wait()


# ─── GitHub releases ───────────────────────────────────────────────────────

def _get(url: str, timeout: float = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": APP_DIR + "-Setup",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_release(data: dict) -> dict | None:
    tag = str(data.get("tag_name") or "").strip()
    if not tag or data.get("draft") or data.get("prerelease") or version_key(tag) == (-1,):
        return None
    asset = next((a.get("browser_download_url") for a in data.get("assets", [])
                  if a.get("name") == RELEASE_ASSET), None)
    return {"version": tag.lstrip("v"), "zip": asset or data.get("zipball_url"), "page": data.get("html_url") or ""}


def latest_release(timeout: float = 10) -> dict | None:
    try:
        return parse_release(json.loads(_get(RELEASE_API, timeout)))
    except Exception:
        return None


def cached_latest(folder, max_age: float = 86400, fetch=latest_release, now: float | None = None) -> dict | None:
    """The newest release, asked at most once per max_age seconds; the answer is kept in update_check.json."""
    path = Path(folder) / UPDATE_CHECK
    now = time.time() if now is None else now
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if "checked" in cache and cache["checked"] + max_age > now:
        return cache.get("release")
    release = fetch()
    try:
        path.write_text(json.dumps({"checked": now, "release": release}), encoding="utf-8")
    except OSError:
        pass
    return release


def download(url: str, dest: Path, progress=None, timeout: float = 30):
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": APP_DIR + "-Setup"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        total, done = int(r.headers.get("Content-Length") or 0), 0
        while chunk := r.read(1 << 16):
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(dest.name, done, total)


def extract_release(zip_path: Path, dest: Path) -> Path:
    """Unpack and return the folder with wt_version.py; entries outside dest are refused."""
    if dest.exists():
        remove_tree(dest)
    dest.mkdir(parents=True)
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            target = (dest / member).resolve()
            if target != root and root not in target.parents:
                raise SetupError("zip_broken", member)
        z.extractall(dest)
    for cand in [dest, *sorted(p for p in dest.iterdir() if p.is_dir())]:
        if (cand / "wt_version.py").is_file():
            return cand
    raise SetupError("zip_broken", "wt_version.py")


# ─── Program files ─────────────────────────────────────────────────────────

def copy_program(src, dst, log=print) -> dict:
    """
    Copy the program files of src (its manifest) to dst. Files that dst has and src replaces go to
    update-backup/<old version>/ first; program files of the old version that the new one has no more are removed.
    """
    src, dst = Path(src), Path(dst)
    new = read_manifest(src)
    if not new:
        raise SetupError("manifest_missing", str(src / MANIFEST))
    old = read_manifest(dst)
    old_version = read_version(dst)
    backup = dst / BACKUP_DIR / (old_version or "old")
    if old and backup.exists():
        remove_tree(backup)
    copied, removed = [], []
    for rel in new:
        s, d = src / rel, dst / rel
        if not s.is_file():
            continue
        if old and d.is_file():
            (backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(d, backup / rel)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        copied.append(rel)
    for rel in sorted(set(old) - set(new)):
        p = dst / rel
        if p.is_file():
            (backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(backup / rel))
            removed.append(rel)
    log(f"copied {len(copied)} files to {dst}, removed {len(removed)}")
    return {"copied": copied, "removed": removed, "backup": str(backup) if old else ""}


def restore_program(dst, version: str, log=print) -> bool:
    backup = Path(dst) / BACKUP_DIR / version
    if not backup.is_dir():
        return False
    for p in sorted(backup.rglob("*")):
        if p.is_file():
            target = Path(dst) / p.relative_to(backup)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
    log(f"restored the program files of {version} from {backup}")
    return True


def unblock(folder, files) -> int:
    """Remove the mark of a download from the internet, so Windows does not warn at every start."""
    if not WINDOWS:
        return 0
    n = 0
    for rel in files:
        try:
            os.remove(str(Path(folder) / rel) + ":Zone.Identifier")
            n += 1
        except OSError:
            pass
    return n


# ─── Removing files ────────────────────────────────────────────────────────

def _long(p: Path) -> str:
    s = os.path.abspath(str(p))
    if not WINDOWS or s.startswith("\\\\?\\"):
        return s
    return "\\\\?\\UNC\\" + s[2:] if s.startswith("\\\\") else "\\\\?\\" + s


def is_link(p: Path) -> bool:
    """A symbolic link or a junction: removed itself, never its target."""
    try:
        st = os.lstat(_long(p))
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def remove_tree(path) -> list[tuple[str, str]]:
    """Remove a file or folder; → [(path, error)] of what could not be removed."""
    path = Path(path)
    failed = []

    def rm(fn, p):
        try:
            fn(_long(p))
        except FileNotFoundError:
            pass
        except PermissionError:
            try:
                os.chmod(_long(p), stat.S_IWRITE)
                fn(_long(p))
            except OSError as e:
                failed.append((str(p), e.strerror or str(e)))
        except OSError as e:
            failed.append((str(p), e.strerror or str(e)))

    if is_link(path):
        rm(os.rmdir if os.path.isdir(_long(path)) and WINDOWS else os.unlink, path)
        return failed
    if not os.path.isdir(_long(path)):
        if os.path.lexists(_long(path)):
            rm(os.unlink, path)
        return failed
    try:
        entries = list(os.scandir(_long(path)))
    except OSError as e:
        return [(str(path), e.strerror or str(e))]
    for e in entries:
        child = path / e.name
        if is_link(child) or not e.is_dir(follow_symlinks=False):
            if is_link(child) and os.path.isdir(_long(child)) and WINDOWS:
                rm(os.rmdir, child)
            else:
                rm(os.unlink, child)
        else:
            failed += remove_tree(child)
    rm(os.rmdir, path)
    return failed


def tree_size(path) -> int:
    path = Path(path)
    if is_link(path):
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not is_link(Path(root) / d)]
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


# ─── Uninstall plan ────────────────────────────────────────────────────────

@dataclass
class Group:
    key: str                     # temp, venv, program, models, resolve, plugin, cache
    paths: list = field(default_factory=list)
    size: int = 0
    default: bool = True
    note: str = ""


def _pycaches(folder: Path) -> list[Path]:
    out = []
    for root, dirs, _ in os.walk(folder):
        rel = Path(root).relative_to(folder)
        if rel.parts and rel.parts[0] in ("venv", ".git", "models") or rel.parts[:1] and \
                rel.parts[0].startswith("venv-"):
            dirs[:] = []
            continue
        for d in list(dirs):
            if d == "__pycache__":
                out.append(Path(root) / d)
                dirs.remove(d)
    return out


def hf_cache(env=os.environ) -> Path:
    if env.get("HF_HUB_CACHE"):
        return Path(env["HF_HUB_CACHE"])
    return Path(env.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"


def torch_cache(env=os.environ) -> Path:
    return Path(env.get("TORCH_HOME") or Path.home() / ".cache" / "torch") / "hub"


def pip_cache(env=os.environ) -> Path:
    if WINDOWS:
        return Path(env.get("LOCALAPPDATA", "")) / "pip" / "Cache"
    return Path(env.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "pip"


# Model folders the Workbench loads: faster-whisper models and the wav2vec2 alignment models of WhisperX.
HF_MODEL_PREFIXES = ("models--Systran--faster-whisper-", "models--mobiuslabsgmbh--faster-whisper-",
                     "models--jonatasgrosman--wav2vec2-")


def model_cache_entries(folder, env=os.environ) -> list[Path]:
    out = []
    hub = hf_cache(env)
    names = list(HF_MODEL_PREFIXES)
    model = ini_value(folder, "Whisper", "model", "")
    if re.fullmatch(r"[\w.-]+/[\w.-]+", model):
        names.append("models--" + model.replace("/", "--"))
    if hub.is_dir():
        out += [p for p in sorted(hub.iterdir()) if p.is_dir() and any(p.name.startswith(n) for n in names)]
    checkpoints = torch_cache(env) / "checkpoints"
    if checkpoints.is_dir():
        out += sorted(checkpoints.glob("wav2vec2_*"))
    return out


def plugin_of(folder) -> dict | None:
    """The installed DaVinci Resolve plugin, if it belongs to this folder."""
    try:
        import wt_resolve
    except Exception:
        return None
    info = wt_resolve.installed_plugin()
    if not info:
        return None
    owner = info.get("workbench") or ""
    if owner and os.path.normcase(os.path.abspath(owner)) != os.path.normcase(os.path.abspath(str(folder))):
        return None
    return info


def foreign_files(folder, manifest: list[str]) -> list[Path]:
    """Files in the folder that neither belong to the Workbench nor were created by it or its installer."""
    folder = Path(folder)
    known = {m.lower() for m in manifest} | {n.lower() for n in KEEP_FILES + TEMP_FILES} \
        | {STATE_FILE.lower(), MANIFEST.lower(), REPORT_FILE.lower(), (STATE_FILE + ".tmp").lower()}
    skip = {"venv", "resolve_temp", "models", ".git", BACKUP_DIR} | set(TEMP_DIRS)
    out = []
    for root, dirs, files in os.walk(folder):
        rel_root = Path(root).relative_to(folder)
        if not rel_root.parts:
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith("venv-")]
        dirs[:] = [d for d in dirs if d != "__pycache__" and not is_link(Path(root) / d)]
        for f in files:
            rel = (rel_root / f).as_posix()
            if rel.lower() not in known:
                out.append(folder / rel)
    return sorted(out)


def uninstall_plan(folder, env=os.environ) -> dict:
    folder = Path(folder)
    manifest = read_manifest(folder)
    state = read_state(folder)
    home = read_pyvenv_cfg(folder / "venv").get("home", "")
    if home and not state.get("python"):
        state["python"] = str(Path(home) / "python.exe")
    groups = {}

    temp = [folder / d for d in TEMP_DIRS] + [folder / f for f in TEMP_FILES] + _pycaches(folder)
    rt = folder / "resolve_temp"
    if rt.is_dir():
        temp += [p for p in rt.iterdir() if p.is_dir()]
    groups["temp"] = Group("temp", [p for p in temp if os.path.lexists(p)])

    groups["venv"] = Group("venv", [p for p in [folder / "venv", *sorted(folder.glob("venv-*"))] if p.exists()])

    git = (folder / ".git").exists()
    program = [folder / m for m in manifest if (folder / m).is_file()]
    if (folder / BACKUP_DIR).exists():
        program.append(folder / BACKUP_DIR)
    groups["program"] = Group("program", program, default=not git, note="git" if git else "")

    groups["models"] = Group("models", [folder / "models"] if (folder / "models").is_dir() else [])
    groups["resolve"] = Group("resolve", [rt] if rt.is_dir() else [])
    info = plugin_of(folder)
    groups["plugin"] = Group("plugin", [Path(info["path"])] if info else [])
    groups["cache"] = Group("cache", model_cache_entries(folder, env), default=False)
    for g in groups.values():
        g.size = sum(tree_size(p) for p in g.paths)
    return {
        "groups": groups,
        "keep": [folder / n for n in KEEP_FILES if (folder / n).is_file()],
        "foreign": foreign_files(folder, manifest),
        "state": state,
        "git": git,
    }


def system_leftovers(folder, state: dict, env=os.environ) -> list[dict]:
    """What stays on the PC after removing the Workbench: software and caches it used."""
    out = []
    by_setup = state.get("installed_by_setup", {})
    py = state.get("python") or ""
    if py:
        out.append({"key": "python", "path": py, "by_setup": bool(by_setup.get("python"))})
    ff = ini_value(folder, "System", "ffmpeg_path", "") or state.get("ffmpeg", "")
    if ff or by_setup.get("ffmpeg"):
        out.append({"key": "ffmpeg", "path": ff, "by_setup": bool(by_setup.get("ffmpeg"))})
    if by_setup.get("vcredist"):
        out.append({"key": "vcredist", "path": "", "by_setup": True})
    for key, path in (("hf_cache", hf_cache(env)), ("torch_cache", torch_cache(env)), ("pip_cache", pip_cache(env))):
        if path.is_dir():
            out.append({"key": key, "path": str(path), "size": tree_size(path)})
    return out


# ─── Windows: shortcuts and the entry under Settings → Apps ───────────────

_SHORTCUT_PS = (
    "$ErrorActionPreference = 'Stop'; $d = [Environment]::GetFolderPath($env:WT_PLACE); "
    "$p = Join-Path $d ($env:WT_NAME + '.lnk'); $s = (New-Object -ComObject WScript.Shell).CreateShortcut($p); "
    "$s.TargetPath = $env:WT_TARGET; $s.WorkingDirectory = $env:WT_DIR; $s.Description = $env:WT_DESC; "
    "if ($env:WT_ICON) { $s.IconLocation = $env:WT_ICON }; $s.WindowStyle = [int]$env:WT_STYLE; $s.Save(); "
    "Write-Output $p")


def create_shortcut(place: str, name: str, target, folder, description: str, icon="", minimized=False) -> str:
    """place: Desktop or Programs (start menu). The values go through the environment: no quoting of paths."""
    if not WINDOWS:
        return ""
    env = dict(os.environ, WT_PLACE=place, WT_NAME=name, WT_TARGET=str(target), WT_DIR=str(folder),
               WT_DESC=description, WT_ICON=str(icon), WT_STYLE="7" if minimized else "1")
    out = _output(["powershell", "-NoProfile", "-NonInteractive", "-Command", _SHORTCUT_PS], timeout=60, env=env)
    path = (out or "").strip().splitlines()[-1] if out and out.strip() else ""
    return path if path and Path(path).is_file() else ""


def register_app(folder, version: str, size_bytes: int, icon=""):
    import winreg
    folder = Path(folder)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, value in (("DisplayName", APP_NAME), ("DisplayVersion", version),
                            ("InstallLocation", str(folder)), ("UninstallString", f'"{folder / "uninstall.bat"}"'),
                            ("ModifyPath", f'"{folder / "install.bat"}"'), ("DisplayIcon", str(icon)),
                            ("URLInfoAbout", PROJECT_URL), ("InstallDate", time.strftime("%Y%m%d"))):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, int(size_bytes // 1024) & 0xFFFFFFFF)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def registered_location() -> Path | None:
    if not WINDOWS:
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            p = Path(_reg_value(k, "InstallLocation"))
    except OSError:
        return None
    return p if str(p) not in ("", ".") and p.is_dir() else None


def unregister_app() -> bool:
    if not WINDOWS:
        return False
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        return True
    except OSError:
        return False


# ─── Tasks that run inside the venv (python wt_setup.py task …) ──────────

VERIFY = r"""
import json
out = {}
try:
    import torch
    out.update(torch=torch.__version__, cuda=torch.version.cuda, cuda_ok=torch.cuda.is_available())
    if out["cuda_ok"]:
        out["gpu"] = torch.cuda.get_device_name(0)
except Exception as e:
    out["torch_error"] = f"{type(e).__name__}: {e}"
for mod in ("torchaudio", "ctranslate2", "faster_whisper", "whisperx"):
    try:
        __import__(mod)
        out[mod] = True
    except Exception as e:
        out[mod] = f"{type(e).__name__}: {e}"
try:
    import ctranslate2
    out["ct2_cuda"] = ctranslate2.get_cuda_device_count()
except Exception:
    pass
print("WT-VERIFY " + json.dumps(out))
"""


def verify_problem(result: dict, build: str) -> str:
    """"" = the installation works; otherwise the key of the problem (text setup.err_<key>)."""
    if "torch_error" in result or "torch" not in result:
        return "torch_import"
    for mod in ("torchaudio", "ctranslate2", "faster_whisper", "whisperx"):
        if result.get(mod) is not True:
            return "import"
    if build != "cpu" and not result.get("cuda_ok"):
        return "no_cuda"
    if local_label(result.get("torch", "")) != build:
        return "wrong_build"
    return ""


def task_ini(folder, device: str = "", compute: str = "") -> dict:
    """Create whisper_tool.ini from the template if needed and set device/compute_type only where the template's
    value still stands (cuda, float16): a value the user chose stays."""
    import wt_core
    folder = Path(folder)
    ini = folder / INI
    if not ini.exists() and (folder / EXAMPLE_INI).exists():
        shutil.copyfile(folder / EXAMPLE_INI, ini)
    values, changed = {}, {}
    if device and ini_value(folder, "System", "device", "cuda").lower() == "cuda" and device != "cuda":
        values[("System", "device")] = device
    if compute and ini_value(folder, "Whisper", "compute_type", "float16").lower() == "float16" \
            and compute != "float16":
        values[("Whisper", "compute_type")] = compute
    if values:
        wt_core.save_ini(ini, values)
        changed = {f"{s}.{k}": v for (s, k), v in values.items()}
    return changed


def task_plugin(folder) -> str:
    import wt_i18n
    import wt_resolve
    from wt_version import VERSION
    wt_i18n.load(wt_i18n.ini_language(Path(folder) / INI))
    return str(wt_resolve.install_plugin(VERSION, wt_i18n.current(), workbench=Path(folder)))


def task_model(folder) -> str:
    import wt_core
    from faster_whisper.utils import download_model
    model = wt_core.Config(Path(folder) / INI).model
    if Path(model).is_dir():
        return model
    return str(download_model(model))


def main(argv: list[str]) -> int:
    """python wt_setup.py task <ini|plugin|model> <folder> [device] [compute] – run by the venv's Python."""
    if len(argv) < 3 or argv[0] != "task":
        print(__doc__)
        return 2
    what, folder = argv[1], Path(argv[2])
    try:
        if what == "ini":
            device, compute = [a if a != "-" else "" for a in (argv[3:5] + ["", ""])[:2]]
            result = task_ini(folder, device, compute)
        elif what == "plugin":
            result = task_plugin(folder)
        elif what == "model":
            result = task_model(folder)
        else:
            return 2
    except Exception as e:
        print(f"WT-ERROR {type(e).__name__}: {e}")
        return 1
    print("WT-RESULT " + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
