from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_TEST = "tests/test_extraction_live.py"


def _collect(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", *args, LIVE_TEST],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def test_live_test_is_excluded_even_with_an_api_key_configured() -> None:
    result = _collect(env={**os.environ, "ORCAROUTER_API_KEY": "test-key"})

    assert result.returncode == 5
    assert "1 deselected" in result.stdout
    assert "test_live_provider" not in result.stdout


def test_live_marker_selects_the_live_test() -> None:
    result = _collect("-m", "live")

    assert result.returncode == 0
    assert "test_live_provider" in result.stdout
