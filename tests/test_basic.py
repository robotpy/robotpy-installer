import pathlib
import sys
import subprocess


def test_download_basic():
    assert 0 == subprocess.check_call(
        [
            sys.executable,
            "-m",
            "robotpy",
            "installer",
            "download",
            "-r",
            str(pathlib.Path(__file__).parent / "sample-requirements.txt"),
        ]
    )
