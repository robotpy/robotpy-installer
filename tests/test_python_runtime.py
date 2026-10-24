import os
import platform
import runpy
import sys
from unittest.mock import Mock

import pytest
from packaging.version import Version

from robotpy_installer import pyproject


@pytest.mark.parametrize(
    "version, expected",
    [
        ("2027.0.0a1", (3, 14, 3)),
        ("2027.0.0a7", (3, 14, 3)),
        ("2027.0.0a8", (3, 15, 1)),
        ("2027.0.0a9", (3, 15, 1)),
        ("2027.0.0a10", (3, 15, 1)),
        ("2027.0.0b1", (3, 15, 1)),
        ("2027.0.0rc1", (3, 15, 1)),
        ("2027.0.0", (3, 15, 1)),
        ("ignored", (3, 15, 1)),
    ],
)
def test_runtime_selection(version, expected):
    project = pyproject.loads(f'[tool.robotpy]\nrobotpy_version = "{version}"')
    assert project.python_runtime.version == expected


@pytest.mark.parametrize(
    "version, expected", [("3.14", (3, 14, 3)), ("3.15", (3, 15, 1))]
)
def test_ignored_runtime_override(version, expected, monkeypatch):
    def no_robotpy():
        raise AssertionError("Must not look up installed RobotPy")

    monkeypatch.setattr(pyproject, "robotpy_installed_version", no_robotpy)
    project = pyproject.loads(
        f'[tool.robotpy]\nrobotpy_version = "ignored"\npython_version = "{version}"'
    )
    assert project.python_runtime.version == expected


@pytest.mark.parametrize(
    "robotpy_version, value",
    [
        ("ignored", '"3.13"'),
        ("ignored", "3.14"),
        ("ignored", "[]"),
        ("2027.0.0a7", '"3.14"'),
        ("2027.0.0a8", '"3.15"'),
    ],
)
def test_invalid_runtime_setting(robotpy_version, value):
    with pytest.raises(pyproject.PyprojectError, match="python_version"):
        pyproject.loads(
            f'[tool.robotpy]\nrobotpy_version = "{robotpy_version}"\npython_version = {value}'
        )


@pytest.mark.parametrize(
    "minor, full, num", [("3.14", "3.14.3", "314"), ("3.15", "3.15.1", "315")]
)
def test_installer_runtime(tmp_path, monkeypatch, minor, full, num):
    from robotpy_installer import installer, pypackages

    project = pyproject.loads(
        f'[tool.robotpy]\nrobotpy_version = "ignored"\npython_version = "{minor}"'
    )
    inst = installer.RobotpyInstaller(
        cache_root=tmp_path, python_runtime=project.python_runtime
    )
    download = Mock()
    monkeypatch.setattr(installer, "_urlretrieve", download)
    inst.download_python(False)
    assert f"cpython-{full}" in download.call_args.args[0]
    assert f"cpython-{full}" in inst._python_pkg_path.name
    call = Mock(return_value=0)
    monkeypatch.setattr(installer.subprocess, "call", call)
    inst.pip_download(False, False, [], ["example"], None)
    args = call.call_args.args[0]
    assert args[args.index("--python-version") + 1] == num
    assert args[args.index("--abi") + 1] == "cp" + num
    assert call.call_args.kwargs["env"]["ROBOTPY_PYTHON_VERSION"] == full
    env = pypackages.robot_env(project.python_runtime)
    assert env["python_version"] == minor
    assert env["python_full_version"] == full


@pytest.mark.parametrize("target", ["3.14.3", "3.15.1", None])
def test_pip_stub_runtime(monkeypatch, target):
    from robotpy_installer import _pipstub

    if target:
        monkeypatch.setenv("ROBOTPY_PYTHON_VERSION", target)
    else:
        monkeypatch.delenv("ROBOTPY_PYTHON_VERSION", raising=False)
    observed = []
    original_run = runpy.run_path
    monkeypatch.setattr(
        runpy, "run_module", lambda *a, **kw: observed.append(platform.python_version())
    )
    # The standalone script mutates platform/sysconfig; restore after execution.
    import sysconfig

    monkeypatch.setattr(platform, "python_version", platform.python_version)
    monkeypatch.setattr(platform, "machine", platform.machine)
    monkeypatch.setattr(platform, "system", platform.system)
    monkeypatch.setattr(
        platform, "python_implementation", platform.python_implementation
    )
    monkeypatch.setattr(sysconfig, "get_platform", sysconfig.get_platform)
    original_run(_pipstub.__file__, run_name="__main__")
    assert observed == [target or ".".join(map(str, sys.version_info[:3]))]


@pytest.mark.parametrize(
    "minor, allowed",
    [("3.14", {"old", "stable", "pure"}), ("3.15", {"new", "stable", "pure"})],
)
def test_mixed_wheel_cache(tmp_path, minor, allowed):
    from robotpy_installer import pypackages

    runtime = pyproject.loads(
        f'[tool.robotpy]\nrobotpy_version="ignored"\npython_version="{minor}"'
    ).python_runtime
    cache = tmp_path / "pip_cache"
    cache.mkdir()
    for name in [
        "old-1-cp314-cp314-linux_aarch64.whl",
        "new-1-cp315-cp315-linux_aarch64.whl",
        "stable-1-cp310-abi3-linux_aarch64.whl",
        "pure-1-py3-none-any.whl",
        "future-1-cp316-abi3-linux_aarch64.whl",
    ]:
        (cache / name).touch()
    assert set(pypackages.get_pip_cache_packages(tmp_path, runtime)) == allowed


@pytest.mark.parametrize(
    "config, expected",
    [
        (None, (3, 15, 1)),
        ('[project]\nname="unrelated"', (3, 15, 1)),
        ('[tool.robotpy]\nrobotpy_version="2027.0.0a7"', (3, 14, 3)),
        (
            '[tool.robotpy]\nrobotpy_version="ignored"\npython_version="3.14"',
            (3, 14, 3),
        ),
    ],
)
def test_standalone_project_runtime(tmp_path, config, expected):
    if config:
        (tmp_path / "pyproject.toml").write_text(config)
    assert pyproject.runtime_for_project(tmp_path).version == expected


def test_standalone_invalid_configuration(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.robotpy]\nrobotpy_version="2027.0.0a7"\npython_version="3.14"'
    )
    with pytest.raises(pyproject.PyprojectError):
        pyproject.runtime_for_project(tmp_path)


def test_runtime_changes_after_project_upgrade():
    project = pyproject.loads('[tool.robotpy]\nrobotpy_version = "2027.0.0a7"')
    assert project.python_runtime.version == (3, 14, 3)
    project.robotpy_version = Version("2027.0.0a8")
    assert project.python_runtime.version == (3, 15, 1)
