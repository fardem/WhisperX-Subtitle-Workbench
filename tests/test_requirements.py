"""requirements.txt and constraints.txt must not contradict each other."""

import re

from helpers import ROOT


def pins(name: str) -> dict:
    out = {}
    for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if "==" in line:
            pkg, ver = line.split("==", 1)
            out[re.sub(r"[-_.]+", "-", pkg).lower()] = ver.strip()
    return out


def test_every_requirement_has_the_same_version_in_constraints():
    req, con = pins("requirements.txt"), pins("constraints.txt")
    assert req, "no pinned packages found in requirements.txt"
    assert {k: (v, con.get(k)) for k, v in req.items() if con.get(k) != v} == {}


def test_torch_is_pinned_without_cuda_build():
    # "+cu128" would reject the cu126 build for older graphics cards
    con = pins("constraints.txt")
    assert con["torch"] == "2.8.0" and con["torchaudio"] == "2.8.0" and con["torchvision"] == "0.23.0"
    assert not any("+" in v for v in con.values())


def test_readme_commands_match_versions():
    con = pins("constraints.txt")
    for readme in ("README.md", "README.de.md", "README.tr.md"):
        text = (ROOT / readme).read_text(encoding="utf-8")
        assert f"torch=={con['torch']} torchaudio=={con['torchaudio']} torchvision=={con['torchvision']}" in text
        assert "-r requirements.txt -c constraints.txt" in text
        assert f"tkinterdnd2=={con['tkinterdnd2']}" in text


def test_drag_and_drop_button_installs_the_pinned_version():
    con = pins("constraints.txt")
    assert f'DND_REQ = "tkinterdnd2=={con["tkinterdnd2"]}"' in (ROOT / "whisper_tool.py").read_text(encoding="utf-8")
