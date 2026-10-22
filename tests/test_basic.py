import subprocess
import sys
import zipfile


def test_download_basic(tmp_path, monkeypatch):
    """Exercise the CLI and pip without relying on published robot wheels."""
    links = tmp_path / "links"
    links.mkdir()
    wheel = links / "runtime_smoke-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "runtime_smoke-1.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: runtime-smoke\nVersion: 1.0\n",
        )
        archive.writestr(
            "runtime_smoke-1.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr("runtime_smoke-1.0.dist-info/RECORD", "")
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    cache = tmp_path / "cache"
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "robotpy",
            "installer",
            "download",
            "--find-links",
            str(links),
            "--cache-root",
            str(cache),
            "runtime-smoke==1.0",
        ],
        cwd=tmp_path,
    )
    assert (cache / "pip_cache" / wheel.name).exists()
