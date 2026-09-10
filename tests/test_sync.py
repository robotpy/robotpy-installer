import argparse
import sys
from unittest.mock import MagicMock

import pytest
from packaging.version import Version

from robotpy_installer import cli_sync
from robotpy_installer.installer import _WPILIB_YEAR as YEAR


@pytest.mark.parametrize("no_install", [False, True])
@pytest.mark.parametrize("no_upgrade_project", [False, True])
@pytest.mark.parametrize("requires", [[], ["example==1.2"], ["robotpy[cscore]"]])
def test_sync_ignored_only_processes_requires(
    tmp_path, monkeypatch, requires, no_install, no_upgrade_project
):
    main_file = tmp_path / "robot.py"
    main_file.write_text("")
    config = (
        '[tool.robotpy]\nrobotpy_version = "ignored"\n'
        f'components = ["cscore"]\nrequires = {requires!r}\n'
    )
    (tmp_path / "pyproject.toml").write_text(config)

    installer = MagicMock()
    monkeypatch.setattr(cli_sync, "RobotpyInstaller", lambda: installer)

    def unexpected_robotpy_access(*args, **kwargs):
        pytest.fail("sync must not inspect or update ignored RobotPy")

    installer.get_pypi_version.side_effect = unexpected_robotpy_access
    monkeypatch.setattr(
        cli_sync.pyproject, "robotpy_installed_version", unexpected_robotpy_access
    )
    monkeypatch.setattr(cli_sync, "yesno", unexpected_robotpy_access)
    monkeypatch.setattr(cli_sync.sys, "platform", "linux")
    execv = MagicMock(side_effect=SystemExit)
    monkeypatch.setattr(cli_sync.os, "execv", execv)

    def run():
        return cli_sync.Sync(argparse.ArgumentParser()).run(
            project_path=tmp_path,
            main_file=main_file,
            find_links=None,
            no_install=no_install,
            no_upgrade_project=no_upgrade_project,
            user=False,
            use_certifi=False,
        )

    if requires and not no_install:
        with pytest.raises(SystemExit):
            run()
        execv.assert_called_once_with(
            sys.executable,
            [sys.executable, "-m", "pip", "--disable-pip-version-check", "install"]
            + requires,
        )
    else:
        assert run() is None
        execv.assert_not_called()

    installer.download_python.assert_called_once_with(False)
    if requires:
        installer.pip_download.assert_called_once_with(
            no_deps=False,
            pre=False,
            requirements=[],
            packages=requires,
            find_links=None,
        )
    else:
        installer.pip_download.assert_not_called()
    installer.pip_wheel.assert_not_called()
    assert (tmp_path / "pyproject.toml").read_text() == config


def test_sync_normal_version_still_upgrades_project(tmp_path, monkeypatch):
    main_file = tmp_path / "robot.py"
    main_file.write_text("")
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.robotpy]\nrobotpy_version = "{YEAR}.1.0"\n'
    )
    installer = MagicMock()
    installer.get_pypi_version.return_value = Version(f"{YEAR}.2.0")
    monkeypatch.setattr(cli_sync, "RobotpyInstaller", lambda: installer)
    monkeypatch.setattr(
        cli_sync.pyproject, "robotpy_installed_version", lambda: f"{YEAR}.1.0"
    )
    monkeypatch.setattr(cli_sync, "yesno", lambda msg: True)

    cli_sync.Sync(argparse.ArgumentParser()).run(
        project_path=tmp_path,
        main_file=main_file,
        find_links=None,
        no_install=True,
        no_upgrade_project=False,
        user=False,
        use_certifi=False,
    )

    installer.get_pypi_version.assert_called_once_with("robotpy", False)
    installer.pip_download.assert_called_once_with(
        no_deps=False,
        pre=False,
        requirements=[],
        packages=[f"robotpy=={YEAR}.2.0"],
        find_links=None,
    )
    assert cli_sync.pyproject.load(tmp_path).robotpy_version == Version(f"{YEAR}.2.0")
