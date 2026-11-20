import importlib
import importlib.metadata
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import subprocess
import sys
import sysconfig
import venv
import zipfile

import pytest

from robotpy_installer import offline
from robotpy_installer._bundle_format import BundleError, validate_bundle
from test_offline_create import creation


@pytest.fixture
def launcher():
    # Keep missing implementation an explicit RED assertion, not collection error.
    assert offline._LAUNCHER_SOURCE.exists(), "standalone launcher is missing"
    return importlib.import_module("robotpy_installer._bundle_main")


@pytest.fixture(scope="session")
def pip_wheel(tmp_path_factory):
    root = tmp_path_factory.mktemp("pip-tool")
    dist = importlib.metadata.distribution("pip")
    path = root / f"pip-{dist.version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in dist.files:
            if str(file).startswith(
                ("pip/", f"pip-{dist.version}.dist-info/")
            ) and "__pycache__" not in str(file):
                archive.write(dist.locate_file(file), str(file))
    return path


@pytest.fixture
def bundle(creation, pip_wheel, monkeypatch):
    project, output, calls = creation
    monkeypatch.setattr(
        offline, "_LAUNCHER_SOURCE", Path(offline.__file__).with_name("_bundle_main.py")
    )
    original = offline._stage_local_wheels

    def stage(*args, **kwargs):
        tool = original(*args, **kwargs)
        tool.unlink()
        target = tool.parent / pip_wheel.name
        target.write_bytes(pip_wheel.read_bytes())
        for wheel in args[1].glob("*.whl"):
            make_wheel(
                wheel, "demo", "1.0", "child @ https://127.0.0.1:9/unreachable.whl"
            )
        make_wheel(args[1] / "child-1.0-py3-none-any.whl", "child", "1.0")
        return target

    monkeypatch.setattr(offline, "_stage_local_wheels", stage)
    return project, output


def make_wheel(path, name, version, dependency=None):
    info = f"{name}-{version}.dist-info"
    files = {
        f"{name}.py": f"__version__ = {version!r}\n",
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        + (f"Requires-Dist: {dependency}\n" if dependency else ""),
        f"{info}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    files[f"{info}/RECORD"] = "".join(f"{p},,\n" for p in [*files, f"{info}/RECORD"])
    with zipfile.ZipFile(path, "w") as archive:
        for p, text in files.items():
            archive.writestr(p, text)


def built(bundle):
    project, output = bundle
    offline.create_bundle(project, output)
    return output, validate_bundle(output)


def test_python_patch_difference_allowed(launcher, bundle, pip_wheel):
    _, manifest = built(bundle)
    manifest["local_python"]["version"][2] += 1
    launcher.check_compatibility(manifest, [], pip_wheel)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("local_python", "version", [2, 7, 0]),
        (
            "local_python",
            "version",
            [sys.version_info.major, sys.version_info.minor + 1, 0],
        ),
        ("local_python", "implementation", "other"),
        ("local_python", "abi_flags", "other"),
        ("platform", "sys_platform", "other"),
        ("platform", "machine", "other"),
    ],
)
def test_incompatible_identity(launcher, bundle, pip_wheel, section, key, value):
    _, manifest = built(bundle)
    manifest[section][key] = value
    with pytest.raises(BundleError, match="compatible"):
        launcher.check_compatibility(manifest, [], pip_wheel)


def test_unusable_tooling_reports_clean_error(launcher, bundle, tmp_path):
    _, manifest = built(bundle)
    broken = tmp_path / "pip-1.0-py3-none-any.whl"
    make_wheel(broken, "pip", "1.0")
    with pytest.raises(BundleError) as error:
        launcher.check_compatibility(manifest, [], broken)
    assert "Traceback" not in str(error.value)
    assert "tooling" in str(error.value)


@pytest.mark.parametrize("incompatible", [False, True])
def test_macos_baseline_difference_uses_wheel_tags(
    launcher, bundle, pip_wheel, monkeypatch, incompatible
):
    _, manifest = built(bundle)
    manifest["platform"].update(
        sys_platform="darwin", machine="arm64", sysconfig_platform="macosx-11.0-arm64"
    )
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(sysconfig, "get_platform", lambda: "macosx-14.0-arm64")
    # Tag validation runs in a real subprocess; universal wheels work on any host.
    wheels = [
        Path(
            "demo-1.0-cp27-cp27m-win32.whl"
            if incompatible
            else "demo-1.0-py3-none-any.whl"
        )
    ]
    if incompatible:
        with pytest.raises(BundleError, match="Incompatible wheel"):
            launcher.check_compatibility(manifest, wheels, pip_wheel)
    else:
        launcher.check_compatibility(manifest, wheels, pip_wheel)


def test_incompatible_wheel_tags(launcher, bundle, pip_wheel):
    _, manifest = built(bundle)
    with pytest.raises(BundleError, match="compatible"):
        launcher.check_compatibility(
            manifest, [Path("demo-1.0-cp27-cp27m-win32.whl")], pip_wheel
        )


def run_main(launcher, output, cache, monkeypatch):
    monkeypatch.setattr(sys, "argv", [str(output)])
    return launcher.main(["--cache-root", str(cache)])


def test_corrupt_bundle_fails_before_install(
    launcher, bundle, tmp_path, monkeypatch, capsys
):
    output, _ = built(bundle)
    with zipfile.ZipFile(output, "a") as archive:
        archive.writestr("unexpected", "bad")
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *a, **k: pytest.fail("installation attempted"),
    )
    assert run_main(launcher, output, tmp_path / "cache", monkeypatch) == 1
    assert "Unexpected" in capsys.readouterr().err
    assert not (tmp_path / "cache").exists()


def test_cache_merge_preserves_unrelated_files(launcher, bundle, tmp_path):
    output, manifest = built(bundle)
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(output) as archive:
        archive.extractall(extracted)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "unrelated").write_text("keep")
    launcher.restore_cache(extracted, manifest, cache)
    assert (cache / "unrelated").read_text() == "keep"
    runtime = Path(manifest["robot_python"]["runtime_path"]).relative_to("robot-cache")
    assert (cache / runtime).read_bytes() == b"runtime"


def symlink_or_skip(link, target, *, target_is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        # Windows requires Developer Mode or SeCreateSymbolicLinkPrivilege.
        # Other permissions/filesystem errors must still fail the test.
        if sys.platform == "win32" and exc.winerror == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise


@pytest.mark.parametrize("location", ["root", "ancestor", "target", "dotdot"])
def test_cache_symlink_rejected(launcher, bundle, tmp_path, monkeypatch, location):
    output, manifest = built(bundle)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    cache = tmp_path / "cache"
    runtime = Path(manifest["robot_python"]["runtime_path"]).relative_to("robot-cache")
    if location == "root":
        symlink_or_skip(cache, elsewhere, target_is_directory=True)
    elif location == "dotdot":
        symlink_or_skip(cache, elsewhere, target_is_directory=True)
        cache = cache / ".." / "nested"
    elif location == "ancestor":
        symlink_or_skip(cache, elsewhere, target_is_directory=True)
        cache = cache / "nested"
    else:
        (cache / runtime).parent.mkdir(parents=True)
        symlink_or_skip(cache / runtime, elsewhere / "missing")
    real = launcher.subprocess.run

    def no_install(command, **kwargs):
        if "install" in command:
            pytest.fail("unsafe cache must fail before installation")
        return real(command, **kwargs)

    monkeypatch.setattr(launcher.subprocess, "run", no_install)
    assert run_main(launcher, output, cache, monkeypatch) == 1
    assert not list(elsewhere.iterdir())


@pytest.mark.parametrize("location", ["root", "ancestor", "target"])
def test_cache_reparse_attribute_rejected(launcher, tmp_path, monkeypatch, location):
    # Model Windows lstat on Python 3.10/3.11, without Path.is_junction.
    # Removing the reparse-attribute guard must make this regression fail.
    root = tmp_path / "cache"
    root.mkdir()
    target = root / "runtime"
    target.write_bytes(b"keep")
    reparse = {"root": root, "ancestor": tmp_path, "target": target}[location]
    real_lstat = Path.lstat

    def lstat(path):
        result = real_lstat(path)
        if path == reparse:
            return SimpleNamespace(
                st_mode=result.st_mode,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return result

    monkeypatch.delattr(Path, "is_junction", raising=False)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(BundleError, match="Unsafe cache"):
        launcher._safe_path(target)
    assert target.read_bytes() == b"keep"


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows junctions")
@pytest.mark.parametrize("location", ["root", "ancestor", "target", "dotdot"])
def test_cache_junction_rejected(
    launcher, bundle, tmp_path, monkeypatch, location, capsys
):
    output, manifest = built(bundle)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    sentinel = elsewhere / "sentinel"
    sentinel.write_bytes(b"keep")
    cache = tmp_path / "cache"
    runtime = Path(manifest["robot_python"]["runtime_path"]).relative_to("robot-cache")
    junction = cache / runtime if location == "target" else cache
    junction.parent.mkdir(parents=True, exist_ok=True)
    # Unlike symlinks, junction creation needs no symlink privilege. Never skip
    # this coverage merely because symlink tests cannot run.
    subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(elsewhere)],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        if location == "ancestor":
            cache = cache / "nested"
        elif location == "dotdot":
            cache = cache / ".." / "nested"
        real = launcher.subprocess.run

        def no_install(command, **kwargs):
            if "install" in command:
                pytest.fail("unsafe cache must fail before installation")
            return real(command, **kwargs)

        monkeypatch.setattr(launcher.subprocess, "run", no_install)
        assert run_main(launcher, output, cache, monkeypatch) == 1
        assert "Unsafe cache" in capsys.readouterr().err
        assert list(elsewhere.iterdir()) == [sentinel]
        assert sentinel.read_bytes() == b"keep"
    finally:
        # Remove only the junction, never recursively traverse its target.
        os.rmdir(junction)
    assert sentinel.read_bytes() == b"keep"


def test_install_failure_does_not_merge_cache(launcher, bundle, tmp_path, monkeypatch):
    output, _ = built(bundle)
    real = launcher.subprocess.run

    def run(command, **kwargs):
        if "install" in command:
            raise subprocess.CalledProcessError(1, command)
        return real(command, **kwargs)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    cache = tmp_path / "cache"
    assert run_main(launcher, output, cache, monkeypatch) == 1
    assert not cache.exists()


def test_pip_config_cannot_redirect_install(launcher, bundle, tmp_path, monkeypatch):
    output, _ = built(bundle)
    monkeypatch.setenv("PIP_TARGET", str(tmp_path / "wrong"))
    monkeypatch.setenv("PIP_BREAK_SYSTEM_PACKAGES", "1")
    real = launcher.subprocess.run
    seen = []

    def run(command, **kwargs):
        if "install" not in command:
            return real(command, **kwargs)
        seen.append(command)
        assert command[0] == sys.executable
        assert "-I" in command
        assert "--no-deps" in command and "--no-index" in command
        assert "--force-reinstall" not in command
        assert {k: v for k, v in kwargs["env"].items() if k.startswith("PIP_")} == {
            "PIP_CONFIG_FILE": os.devnull
        }
        wheels = [
            Path(p) for p in command if p.endswith(".whl") and "local" in Path(p).parts
        ]
        assert len(wheels) == 2 and all(p.is_file() for p in wheels)
        assert not any("https://" in p for p in command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert run_main(launcher, output, tmp_path / "cache", monkeypatch) == 0
    assert seen


def test_empty_inventory_skips_pip_and_uses_default_cache(
    launcher, creation, pip_wheel, tmp_path, monkeypatch
):
    project, output, _ = creation
    project.write_text('[tool.robotpy]\nrobotpy_version="ignored"\nrequires=[]\n')
    original = offline._stage_local_wheels

    def stage(*args, **kwargs):
        tool = original(*args, **kwargs)
        tool.unlink()
        target = tool.parent / pip_wheel.name
        target.write_bytes(pip_wheel.read_bytes())
        return target

    monkeypatch.setattr(offline, "_stage_local_wheels", stage)
    offline.create_bundle(project, output)
    manifest = validate_bundle(output)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", [str(output)])
    real = launcher.subprocess.run

    def no_install(command, **kwargs):
        assert "install" not in command
        return real(command, **kwargs)

    monkeypatch.setattr(launcher.subprocess, "run", no_install)
    assert launcher.main([]) == 0
    runtime = Path(manifest["robot_python"]["runtime_path"]).relative_to("robot-cache")
    assert (
        tmp_path / "wpilib" / str(manifest["cache_year"]) / "robotpy" / runtime
    ).read_bytes() == b"runtime"


@pytest.mark.parametrize(
    "existing",
    [False, True],
    ids=["fresh-direct-url-cache-override", "replaces-differing-version"],
)
def test_bundle_runs_in_venv_without_pip(launcher, bundle, tmp_path, existing):
    output, manifest = built(bundle)
    envdir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False).create(envdir)
    python = envdir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = Path(
        subprocess.check_output(
            [
                str(python),
                "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))",
            ],
            text=True,
        ).strip()
    )
    (site / "unrelated.py").write_text("value = 42")
    # Network denial also covers the -I bootstrap: sitecustomize lives in this venv.
    (site / "sitecustomize.py").write_text(
        "import socket\ndef denied(*a, **k): raise RuntimeError('network forbidden')\nsocket.socket.connect = denied\nsocket.create_connection = denied\n"
    )
    if existing:
        old = tmp_path / "demo-2.0-py3-none-any.whl"
        make_wheel(old, "demo", "2.0")
        with zipfile.ZipFile(old) as archive:
            archive.extractall(site)
    cache = tmp_path / "custom-cache"
    cache.mkdir()
    (cache / "keep").write_text("keep")
    config = tmp_path / "pip.conf"
    config.write_text(
        "[global]\nindex-url = https://127.0.0.1:9\n[install]\ntarget = "
        + str(tmp_path / "wrong")
    )
    (envdir / ("pip.ini" if os.name == "nt" else "pip.conf")).write_text(
        config.read_text()
    )
    env = dict(
        os.environ,
        PIP_CONFIG_FILE=str(config),
        PIP_TARGET=str(tmp_path / "wrong"),
        PIP_USER="1",
        PYTHONPATH="",
    )
    for _ in range(2):
        result = subprocess.run(
            [str(python), str(output), "--cache-root", str(cache)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.check_output(
        [
            str(python),
            "-c",
            "import demo, child, unrelated; print(demo.__version__, child.__version__, unrelated.value); import importlib.util; assert importlib.util.find_spec('pip') is None; assert importlib.util.find_spec('robotpy_installer') is None",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
    )
    assert result.strip() == "1.0 1.0 42"
    assert (cache / "keep").read_text() == "keep"
    assert (
        cache
        / Path(manifest["robot_python"]["runtime_path"]).relative_to("robot-cache")
    ).read_bytes() == b"runtime"
    assert not (tmp_path / "wrong").exists()
