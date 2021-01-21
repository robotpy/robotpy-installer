import sys
import subprocess


def test_download_basic():
    assert 0 == subprocess.check_call(
        [sys.executable, "-m", "robotpy_installer", "download", "robotpy[all]"]
    )
