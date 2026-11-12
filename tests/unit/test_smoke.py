import subprocess
import sys

import gnsskit
from gnsskit.cli import main


def test_package_exposes_version():
    assert gnsskit.__version__ == "0.1.0"


def test_cli_main_returns_zero_with_no_arguments():
    assert main([]) == 0


def test_cli_version_flag_runs_as_module():
    result = subprocess.run(
        [sys.executable, "-m", "gnsskit.cli", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "gnsskit 0.1.0"
