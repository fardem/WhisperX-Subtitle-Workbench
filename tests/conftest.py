"""
Shared helpers for the tests. They need only numpy and pytest – no GPU, no WhisperX:
WhisperX results are built by hand, audio is synthesized.

    venv\\Scripts\\python.exe -m pip install pytest
    venv\\Scripts\\python.exe -m pytest
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import wt_i18n  # noqa: E402

wt_i18n.load("en")


@pytest.fixture(autouse=True)
def english():
    """Every test sees the English texts, whatever the system language is."""
    wt_i18n.load("en")
    yield
