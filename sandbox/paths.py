"""Shared paths for package data and generated outputs."""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
RESULTS_DIR = PACKAGE_ROOT / "results"


def result_path(name: str) -> Path:
    """Return a report path, creating the results directory when needed."""
    RESULTS_DIR.mkdir(exist_ok=True)
    return RESULTS_DIR / name
