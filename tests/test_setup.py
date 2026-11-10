"""install.bat and uninstall.bat: decisions of wt_setup.py without Windows, network or graphics card."""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import wt_setup as s
from helpers import ROOT

PINS = s.read_pins(ROOT / "constraints.txt")


# ─── Versions ────────────────────────────────────────────────────────────────

def test_pep440_order():
    order = ["1.0.dev0", "1.0a1", "1.0b2", "1.0rc1", "1.0", "1.0.post1", "1.0.1", "1.1"]
    keys = [s.version_key(v) for v in order]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)


@pytest.mark.parametrize("a, b", [("2.8.0+cu128", "2.8.0"), ("2.8", "2.8.0"), ("v0.8.0", "0.8.0")])
def test_equal_versions(a, b):
    assert s.version_key(a) == s.version_key(b)


@pytest.mark.parametrize("a, b", [("2026.1.post1", "2026.1"), ("0.62", "0.62b0"), ("1.3.1.1", "1.3.1"),
                                  ("2.9.1", "2.8.0+cu128"), ("26.1.0", "26.0")])
def test_newer(a, b):
    assert s.newer(a, b) and not s.newer(b, a)


def test_every_pinned_version_is_understood():
    assert [v for v in PINS.values() if s.version_key(v) == (-1,)] == []


def test_local_label():
    assert s.local_label("2.8.0+CU128") == "cu128" and s.local_label("2.8.0") == ""


def test_repository_version_is_read_without_import():
    import wt_version
    assert s.read_version(ROOT) == wt_version.VERSION


# ─── Packages ────────────────────────────────────────────────────────────────

def installed_like_tested(build="cu128"):
    return {name: f"{v}+{build}" if name in s.TORCH_PKGS else v for name, v in PINS.items()}


def test_tested_installation_needs_no_change():
    inst = installed_like_tested()
    assert [c for c in s.plan_changes(inst, PINS, "cu128") if c.kind != "foreign"] == []
    assert s.torch_needs_work(inst, PINS, "cu128") == ""


def test_newer_older_missing_and_foreign_packages():
    inst = installed_like_tested()
    inst.update(torch="2.9.1+cpu", numpy="2.3.4", certifi="2026.9.1", openai="1.2.3")
    del inst["ctranslate2"]
    kinds = {c.name: c.kind for c in s.plan_changes(inst, PINS, "cu128")}
    assert kinds == {"torch": "downgrade", "numpy": "downgrade", "certifi": "update", "ctranslate2": "new",
                     "openai": "foreign"}
    assert s.torch_needs_work(inst, PINS, "cu128") == "install"


def test_cpu_build_of_the_right_version_is_a_rebuild():
    inst = installed_like_tested("cpu")
    changes = s.by_kind(s.plan_changes(inst, PINS, "cu128"))
    assert sorted(c.name for c in changes["rebuild"]) == sorted(s.TORCH_PKGS)
    assert s.torch_needs_work(inst, PINS, "cu128") == "rebuild"
    assert s.torch_needs_work(installed_like_tested("cu126"), PINS, "cu126") == ""


def test_dry_run_report_decides_the_version_but_not_for_torch():
    inst = installed_like_tested()
    report = {"numpy": "2.2.6", "torch": "2.8.0", "newpkg": "1.0"}
    inst["numpy"] = "2.3.0"
    kinds = {c.name: (c.kind, c.new) for c in s.plan_changes(inst, PINS, "cu128", report)}
    assert kinds == {"numpy": ("downgrade", "2.2.6"), "newpkg": ("new", "1.0")}


def test_pip_report():
    text = json.dumps({"install": [{"metadata": {"name": "numpy", "version": "2.2.6"}},
                                   {"metadata": {"name": "torch", "version": "2.8.0"}}]})
    assert s.parse_pip_report(text) == {"numpy": "2.2.6", "torch": "2.8.0"}


def test_report_compares_before_and_after_the_run():
    before = {"torch": "2.8.0+cpu", "numpy": "2.3.4", "certifi": "2026.9.1", "old": "1.0", "same": "1"}
    after = {"torch": "2.8.0+cu128", "numpy": "2.2.6", "certifi": "2026.10.23", "tkinterdnd2": "0.7.0", "same": "1"}
    kinds = {c.name: c.kind for c in s.compare_installed(before, after)}
    assert kinds == {"torch": "rebuild", "numpy": "downgrade", "certifi": "update", "tkinterdnd2": "new",
                     "old": "removed"}


def test_names_are_compared_normalized():
    assert s.compare_installed({"PyYAML": "6.0.3", "typing_extensions": "4.15.0"},
                               {"pyyaml": "6.0.3", "typing-extensions": "4.15.0"}) == []


# ─── Graphics card ───────────────────────────────────────────────────────────

SMI = """NVIDIA GeForce RTX 5080, 581.57, 12.0
NVIDIA GeForce GTX 1080, 581.57, 6.1"""


def test_nvidia_smi_lines():
    gpus = s.parse_nvidia_smi(SMI)
    assert [(g.name, g.driver, g.cc) for g in gpus] == [("NVIDIA GeForce RTX 5080", "581.57", (12, 0)),
                                                         ("NVIDIA GeForce GTX 1080", "581.57", (6, 1))]
    assert s.choose_build(gpus)[0] == "cu128"


@pytest.mark.parametrize("line, build", [
    ("NVIDIA GeForce RTX 2060, 572.16, 7.5", "cu128"),
    ("NVIDIA GeForce GTX 1660 SUPER, 572.16, 7.5", "cu128"),
    ("NVIDIA GeForce GTX 1080 Ti, 572.16, 6.1", "cu126"),
    ("NVIDIA TITAN V, 572.16, 7.0", "cu126"),
    ("NVIDIA GeForce GTX 970, 572.16, 5.2", "cu126"),
    ("NVIDIA GeForce GTX 780, 474.44, 3.5", "cpu"),
    ("NVIDIA GeForce GTX 1060 6GB, 497.29", "cu126"),
    ("NVIDIA GeForce RTX 3070, 497.29", "cu128"),
    ("NVIDIA RTX A4000, 572.16, 8.6", "cu128"),
])
def test_build_by_compute_capability(line, build):
    assert s.choose_build(s.parse_nvidia_smi(line))[0] == build


def test_no_nvidia_card_means_cpu():
    assert s.choose_build([]) == ("cpu", None)
    assert s.parse_nvidia_smi("NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.") == []


@pytest.mark.parametrize("driver, major", [("581.57", 581), ("552.44", 552), ("", None)])
def test_driver_major(driver, major):
    assert s.driver_major(driver) == major


# ─── Python ──────────────────────────────────────────────────────────────────

def py(version, **kw):
    return s.PythonInfo(Path(kw.pop("path", f"C:/Python{version[0]}{version[1]}/python.exe")), version,
                        kw.pop("bits", 64), kw.pop("tk", True), True, kw.pop("ft", False))


@pytest.mark.parametrize("info, problem", [
    (py((3, 13, 9)), ""),
    (py((3, 10, 11)), ""),
    (py((3, 14, 0)), "new"),
    (py((3, 9, 13)), "old"),
    (py((3, 13, 9), bits=32), "bits"),
    (py((3, 13, 9), ft=True), "freethreaded"),
    (py((3, 13, 9), tk=False), "tkinter"),
    (py((3, 13, 9), path="C:/Users/a/AppData/Local/Microsoft/WindowsApps/PythonSoftwareFoundation.Python.3.13_q/"
                         "python.exe".replace("/", "\\")), "store"),
])
def test_python_problems(info, problem):
    assert info.problem() == problem


def test_python_choice_prefers_313_then_the_newest():
    cands = [py((3, 14, 0)), py((3, 12, 10)), py((3, 13, 5)), py((3, 13, 9), path="C:/b/python.exe"),
             py((3, 11, 0))]
    assert s.choose_python(cands).version == (3, 13, 9)
    assert s.choose_python([py((3, 11, 2)), py((3, 12, 1))]).version == (3, 12, 1)
    assert s.choose_python([py((3, 14, 0))]) is None


def test_probe_of_this_python():
    info = s.probe_python(sys.executable)
    assert info is not None and info.version[:2] == sys.version_info[:2] and info.bits in (32, 64)


# ─── Folders ─────────────────────────────────────────────────────────────────

def test_path_warnings(tmp_path):
    env = {"OneDrive": str(tmp_path / "OneDrive"), "TEMP": str(tmp_path / "Temp")}
    assert s.path_warnings(tmp_path / "Wb", env, long_paths=True) == []
    assert s.path_warnings(tmp_path / "OneDrive" / "Wb", env, True) == ["onedrive"]
    assert s.path_warnings(tmp_path / "Temp" / "x" / "Wb", env, True) == ["temp"]
    assert "non_ascii" in s.path_warnings(tmp_path / "Jürgen" / "Wb", env, True)
    long = tmp_path / ("x" * (s.LONG_PATH + 1))
    assert "long" in s.path_warnings(long, env, long_paths=False)
    assert "long" not in s.path_warnings(long, env, long_paths=True)


def test_long_path_limit_leaves_room_for_the_longest_file_of_the_venv():
    # torch/include/ATen/native/transformers/cuda/mem_eff_attention/iterators/predicated_tile_access_iterator_
    # residual_last.h: 119 characters in the torch 2.8.0 wheel, plus "venv\\Lib\\site-packages\\"
    assert s.LONG_PATH + 1 + 119 + len("venv\\Lib\\site-packages\\") < 260


def test_default_target_name():
    assert s.default_target().name == "WhisperX-Subtitle-Workbench"


# ─── ffmpeg ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line, version, major, ok", [
    ("ffmpeg version 7.1-full_build-www.gyan.dev Copyright (c) 2000-2024", "7.1-full_build-www.gyan.dev", 7, True),
    ("ffmpeg version n9.0 Copyright", "n9.0", 9, True),
    ("ffmpeg version 5.1.2 Copyright", "5.1.2", 5, False),
    ("ffmpeg version N-118896-g1234 Copyright", "N-118896-g1234", None, True),
    ("ffmpeg version 2025-01-02-git-abc-full_build", "2025-01-02-git-abc-full_build", None, True),
    ("", "", None, False),
])
def test_ffmpeg_versions(line, version, major, ok):
    assert s.parse_ffmpeg_version(line) == (version, major)
    assert s.ffmpeg_ok(version, major) is ok


# ─── Commands ────────────────────────────────────────────────────────────────

def test_torch_command_is_the_one_of_the_readme():
    cmd = " ".join(s.torch_command("python", "cu128", PINS))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    pkgs = f"torch=={PINS['torch']} torchaudio=={PINS['torchaudio']} torchvision=={PINS['torchvision']}"
    assert pkgs in cmd and pkgs in readme
    assert "--index-url https://download.pytorch.org/whl/cu128" in cmd
    assert "https://download.pytorch.org/whl/cu128" in readme
    assert "--progress-bar raw" in " ".join(s.torch_command("python", "cu126", PINS, (26, 3)))


def test_requirements_command_uses_both_files():
    cmd = s.requirements_command("python", ROOT)
    assert cmd[cmd.index("-r") + 1].endswith("requirements.txt") and cmd[cmd.index("-c") + 1].endswith(
        "constraints.txt")
    dry = s.dry_run_command("python", ROOT)
    assert "--dry-run" in dry and dry[dry.index("--report") + 1] == "-"


def test_drag_and_drop_package_without_dependencies():
    assert s.dnd_command("python", PINS)[-2:] == ["--no-deps", f"tkinterdnd2=={PINS['tkinterdnd2']}"]


def test_pip_runs_only_inside_a_venv():
    env = s.clean_env({"PYTHONPATH": "x", "VIRTUAL_ENV": "y", "PATH": "p"})
    assert env["PIP_REQUIRE_VIRTUALENV"] == "1" and env["PYTHONNOUSERSITE"] == "1"
    assert "PYTHONPATH" not in env and "VIRTUAL_ENV" not in env and env["PATH"] == "p"


def test_winget_installs_python_for_the_user_only():
    assert s.winget_command("python")[-2:] == ["--scope", "user"]
    assert "--scope" not in s.winget_command("ffmpeg")


def test_ipc_port_is_the_one_of_the_workbench():
    wt = pytest.importorskip("whisper_tool")
    assert s.IPC_ADDR == wt.IPC_ADDR


def test_run_reads_pip_progress(tmp_path):
    script = tmp_path / "p.py"
    script.write_text("print('Downloading torch-2.8.0.whl (3.0 GB)')\nprint('Progress 5 of 10')\nprint('done')\n")
    lines, prog = [], []
    rc = s.run([sys.executable, str(script)], lines.append, lambda n, a, b: prog.append((n, a, b)))
    assert rc == 0 and prog == [("torch-2.8.0.whl", 5, 10)] and lines[-1] == "done"


# ─── Python environment ──────────────────────────────────────────────────────

def test_missing_and_broken_venv(tmp_path):
    assert s.inspect_venv(tmp_path).exists is False
    (tmp_path / "venv").mkdir()
    assert s.inspect_venv(tmp_path).problem == "no_python"
    py = s.venv_python(tmp_path)
    py.parent.mkdir(parents=True)
    py.write_text("")
    (tmp_path / "venv" / "pyvenv.cfg").write_text(f"home = {tmp_path / 'gone'}\nversion = 3.12.1\n")
    assert s.inspect_venv(tmp_path).problem == "home_missing"


def test_a_real_venv_without_pip(tmp_path):
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(tmp_path / "venv")], check=True)
    v = s.inspect_venv(tmp_path)
    assert v.exists and v.info is not None and v.info.version[:2] == sys.version_info[:2]
    assert v.problem in ("", "tkinter")
    assert isinstance(v.installed, dict)


def test_scenarios():
    pins = {"torch": "2.8.0", "torchaudio": "2.8.0", "torchvision": "0.23.0", "numpy": "2.2.6"}
    ok = s.VenvState(True, "", None, {"torch": "2.8.0+cu128", "torchaudio": "2.8.0+cu128",
                                      "torchvision": "0.23.0+cu128", "numpy": "2.2.6", "pip": "26.0"})
    assert s.classify(s.VenvState(), {}, pins, "cu128", "h", False).code == "new"
    assert s.classify(s.VenvState(), {}, pins, "cu128", "h", True).code == "parent"
    assert s.classify(s.VenvState(True, "home_missing"), {}, pins, "cu128", "h", False).code == "broken"
    assert s.classify(ok, {}, pins, "cu128", "h", False).code == "ok"
    assert s.classify(ok, {"phase": "started"}, pins, "cu128", "h", False).code == "incomplete"
    assert s.classify(ok, {}, pins, "cu126", "h", False).code == "changes"
    newer_numpy = s.VenvState(True, "", None, dict(ok.installed, numpy="2.3.0"))
    sc = s.classify(newer_numpy, {}, pins, "cu128", "h", False)
    assert sc.code == "changes" and [c.kind for c in sc.changes] == ["downgrade"]
    assert ok.pip == (26, 0)


def test_verify_result():
    good = {"torch": "2.8.0+cu128", "cuda_ok": True, "torchaudio": True, "ctranslate2": True,
            "faster_whisper": True, "whisperx": True}
    assert s.verify_problem(good, "cu128") == ""
    assert s.verify_problem(dict(good, cuda_ok=False), "cu128") == "no_cuda"
    assert s.verify_problem(dict(good, torch="2.8.0+cpu", cuda_ok=False), "cpu") == ""
    assert s.verify_problem(dict(good, whisperx="ImportError: x"), "cu128") == "import"
    assert s.verify_problem({"torch_error": "OSError: DLL"}, "cu128") == "torch_import"


# ─── State, manifest, INI ────────────────────────────────────────────────────

def test_manifest_lists_the_tracked_files():
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    sys.path.insert(0, str(ROOT / "tools"))
    import manifest
    try:
        tracked = manifest.tracked()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available")
    assert s.read_manifest(ROOT) == tracked, "python tools/manifest.py --write"


def test_manifest_holds_what_the_installer_needs():
    files = set(s.read_manifest(ROOT))
    assert {"install.bat", "uninstall.bat", "wt_setup.py", "wt_setup_ui.py", "Whisper_Tool.bat", "requirements.txt",
            "constraints.txt", "whisper_tool.example.ini", "lang/en.json"} <= files
    assert not files & {"whisper_tool.ini", s.STATE_FILE}


def test_state_file(tmp_path):
    s.write_state(tmp_path, phase="started", build="cu128")
    s.write_state(tmp_path, phase="complete")
    assert s.read_state(tmp_path) == {"phase": "complete", "build": "cu128"}
    assert s.requirements_hash(ROOT) == s.requirements_hash(ROOT) and len(s.requirements_hash(ROOT)) == 16


def test_ini_defaults_only_replace_template_values(tmp_path):
    (tmp_path / s.EXAMPLE_INI).write_text((ROOT / s.EXAMPLE_INI).read_text(encoding="utf-8"), encoding="utf-8")
    assert s.task_ini(tmp_path, "cpu", "int8") == {"System.device": "cpu", "Whisper.compute_type": "int8"}
    text = (tmp_path / s.INI).read_text(encoding="utf-8")
    assert "device = cpu" in text and "compute_type = int8" in text and "; device:" in text
    assert s.task_ini(tmp_path, "cpu", "int8") == {}
    (tmp_path / s.INI).write_text(text.replace("compute_type = int8", "compute_type = float32"), encoding="utf-8")
    assert s.task_ini(tmp_path, "", "int8") == {}


# ─── Releases and program files ──────────────────────────────────────────────

def test_release_from_the_github_answer():
    data = {"tag_name": "0.8.1", "html_url": "https://x/0.8.1", "zipball_url": "https://x/zipball",
            "assets": [{"name": s.RELEASE_ASSET, "browser_download_url": "https://x/asset.zip"}]}
    assert s.parse_release(data) == {"version": "0.8.1", "zip": "https://x/asset.zip", "page": "https://x/0.8.1"}
    assert s.parse_release(dict(data, assets=[]))["zip"] == "https://x/zipball"
    assert s.parse_release(dict(data, prerelease=True)) is None
    assert s.parse_release({"tag_name": "nightly"}) is None


def test_update_check_at_most_once_a_day(tmp_path):
    calls = []

    def fetch():
        calls.append(1)
        return {"version": "0.9.0"}
    assert s.cached_latest(tmp_path, fetch=fetch, now=1000)["version"] == "0.9.0"
    assert s.cached_latest(tmp_path, fetch=fetch, now=1000 + 3600)["version"] == "0.9.0"
    assert len(calls) == 1
    s.cached_latest(tmp_path, fetch=fetch, now=1000 + 86401)
    assert len(calls) == 2


def make_version(folder: Path, version: str, files: dict):
    folder.mkdir(parents=True, exist_ok=True)
    files = dict(files, **{"wt_version.py": f'VERSION = "{version}"\n'})
    for rel, text in files.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text, encoding="utf-8")
    (folder / s.MANIFEST).write_text("\n".join(sorted(files) + [s.MANIFEST]) + "\n", encoding="utf-8")


def test_extract_release(tmp_path):
    src = tmp_path / "src" / "WhisperX-Subtitle-Workbench"
    make_version(src, "0.9.0", {"a.py": "x"})
    zpath = tmp_path / "r.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for p in src.rglob("*"):
            z.write(p, p.relative_to(src.parent).as_posix())
    folder = s.extract_release(zpath, tmp_path / "out")
    assert s.read_version(folder) == "0.9.0"
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../evil.txt", "x")
    with pytest.raises(s.SetupError):
        s.extract_release(bad, tmp_path / "out2")


def test_copy_program_keeps_the_settings_and_backs_up(tmp_path):
    old, new = tmp_path / "inst", tmp_path / "new"
    make_version(old, "0.8.0", {"a.py": "old", "gone.py": "old", "lang/en.json": "{}"})
    (old / s.INI).write_text("[System]\n", encoding="utf-8")
    (old / "my.mp4").write_text("media", encoding="utf-8")
    make_version(new, "0.8.1", {"a.py": "new", "lang/en.json": "{}", "b.py": "new"})
    result = s.copy_program(new, old, log=lambda m: None)
    assert (old / "a.py").read_text() == "new" and (old / "b.py").exists() and not (old / "gone.py").exists()
    assert (old / s.INI).read_text() == "[System]\n" and (old / "my.mp4").exists()
    assert (old / s.BACKUP_DIR / "0.8.0" / "a.py").read_text() == "old" and result["removed"] == ["gone.py"]
    assert s.restore_program(old, "0.8.0", log=lambda m: None)
    assert (old / "a.py").read_text() == "old" and (old / "gone.py").exists()


# ─── Removing ────────────────────────────────────────────────────────────────

def test_remove_tree_with_read_only_file_and_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    d = tmp_path / "venv" / "Lib"
    d.mkdir(parents=True)
    ro = d / "ro.txt"
    ro.write_text("x")
    os.chmod(ro, 0o444)
    try:
        os.symlink(outside, tmp_path / "venv" / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pass
    assert s.remove_tree(tmp_path / "venv") == []
    assert not (tmp_path / "venv").exists() and (outside / "keep.txt").exists()


def make_installation(folder: Path) -> Path:
    make_version(folder, "0.8.0", {"whisper_tool.py": "x", "lang/en.json": "{}", "tools/x.py": "x"})
    (folder / s.INI).write_text("[System]\n", encoding="utf-8")
    (folder / "venv" / "Lib").mkdir(parents=True)
    (folder / "venv" / "Lib" / "a.py").write_text("x" * 100)
    (folder / "__pycache__").mkdir()
    (folder / "tools" / "__pycache__").mkdir()
    (folder / s.LOG_FILE).write_text("log")
    rt = folder / "resolve_temp"
    (rt / "job1").mkdir(parents=True)
    (rt / "Timeline_DE_20261110.srt").write_text("1")
    (rt / "plugin.txt").write_text("x")
    (folder / "models" / "tr").mkdir(parents=True)
    (folder / "interview.mp4").write_text("media")
    (folder / "Projekte").mkdir()
    (folder / "Projekte" / "folge1.wav").write_text("audio")
    return folder


def test_uninstall_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "plugin_of", lambda folder: None)
    folder = make_installation(tmp_path / "wb")
    plan = s.uninstall_plan(folder, env={})
    g = plan["groups"]

    def rel(key):
        return sorted(p.relative_to(folder).as_posix() for p in g[key].paths)
    assert rel("temp") == ["__pycache__", "install.log", "resolve_temp/job1", "tools/__pycache__"]
    assert rel("venv") == ["venv"] and rel("models") == ["models"] and rel("resolve") == ["resolve_temp"]
    assert "whisper_tool.py" in rel("program") and s.INI not in rel("program")
    assert [p.name for p in plan["keep"]] == [s.INI]
    assert sorted(p.relative_to(folder).as_posix() for p in plan["foreign"]) == ["Projekte/folge1.wav",
                                                                               "interview.mp4"]
    assert g["program"].default and not g["cache"].default and g["venv"].size == 100


def test_a_git_clone_keeps_its_program_files(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "plugin_of", lambda folder: None)
    folder = make_installation(tmp_path / "wb")
    (folder / ".git").mkdir()
    plan = s.uninstall_plan(folder, env={})
    assert plan["git"] and not plan["groups"]["program"].default


# ─── BAT files ───────────────────────────────────────────────────────────────

BATS = ["install.bat", "uninstall.bat", "Whisper_Tool.bat"]
PATH_VARS = ("%WT%", "%~dp0", "%PY%", "%PYW%", "%NOTK%", "%~1")


def bat_lines(name):
    return (ROOT / name).read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("name", BATS)
def test_bat_has_no_blocks_and_no_delayed_expansion(name):
    # a ")" in a folder name would end a ( ) block; "!" in a folder name would be lost with delayed expansion
    lines = bat_lines(name)
    assert [line for line in lines if line.rstrip().endswith("(") and line.strip().lower() != "echo("] == []
    assert not any("enabledelayedexpansion" in line.lower() for line in lines)


@pytest.mark.parametrize("name", BATS)
def test_bat_paths_are_quoted(name):
    import re
    wrong = []
    for line in bat_lines(name):
        if re.match(r"\s*(echo|rem|set |:)", line, re.I):
            continue
        outside = re.sub(r'"[^"]*"', "", line)
        if any(v in outside for v in PATH_VARS):
            wrong.append(line)
    assert wrong == []


@pytest.mark.parametrize("name", ["install.bat", "uninstall.bat"])
def test_bat_texts_in_three_languages(name):
    import re
    text = "\n".join(bat_lines(name))
    parts = re.split(r"^:texts_(?:de|tr)$", text.split("\n:texts\n", 1)[1], flags=re.M)
    names = [sorted(set(re.findall(r'^set "(T_\w+)=', p, re.M))) for p in parts]
    assert len(names) == 3 and names[0] == names[1] == names[2] and names[0]


def test_bat_files_get_windows_line_ends():
    assert "*.bat text eol=crlf" in (ROOT / ".gitattributes").read_text(encoding="utf-8")


def test_starter_offers_the_installation():
    text = "\n".join(bat_lines("Whisper_Tool.bat"))
    assert 'call "%~dp0install.bat" not-installed' in text
    assert "not-installed" in "\n".join(bat_lines("install.bat"))
