"""教程配套的实验脚本（labs/）必须始终能跑通——它们是教材的一部分。"""

import pathlib
import subprocess
import sys

import pytest

LABS_DIR = pathlib.Path(__file__).resolve().parent.parent / "labs"
LABS = sorted(p.name for p in LABS_DIR.glob("lab*.py"))


def test_there_are_twelve_labs():
    assert len(LABS) == 12


@pytest.mark.parametrize("lab", LABS)
def test_lab_runs_to_completion(lab):
    result = subprocess.run([sys.executable, str(LABS_DIR / lab)], cwd=LABS_DIR,
                            capture_output=True, timeout=300)
    output = result.stdout.decode("utf-8", "replace")
    assert result.returncode == 0, output[-3000:] + result.stderr.decode("utf-8", "replace")[-3000:]
    assert "完成。" in output.splitlines()[-1]
