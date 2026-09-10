import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify_offline_content_flow.py"


def test_offline_verifier_runs_without_site_packages():
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Offline content-source ingestion and gallery projection passed." in result.stdout
