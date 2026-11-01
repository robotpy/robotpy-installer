import argparse
from unittest.mock import MagicMock

import pytest

from robotpy_installer import cli_deploy, pyproject, pypackages
from robotpy_installer.installer import _WPILIB_YEAR as YEAR


@pytest.mark.parametrize(
    "config",
    [
        'robotpy_version="2027.0.0a7"',
        'robotpy_version="ignored"\npython_version="3.14"',
    ],
)
def test_no_install_run_uses_project_runtime(tmp_path, monkeypatch, capsys, config):
    from contextlib import nullcontext
    from robotpy_installer.installer import RobotpyInstaller

    (tmp_path / "pyproject.toml").write_text("[tool.robotpy]\n" + config)
    main_file = tmp_path / "robot.py"
    main_file.write_text("")
    installer = RobotpyInstaller(cache_root=tmp_path)
    monkeypatch.setattr(
        installer, "connect_to_robot", lambda **kw: nullcontext(MagicMock())
    )
    monkeypatch.setattr(installer, "is_python_installed", lambda: True)
    monkeypatch.setattr(installer, "get_python_version", lambda: (3, 14))
    monkeypatch.setattr(cli_deploy, "RobotpyInstaller", lambda **kw: installer)
    monkeypatch.setattr(cli_deploy.robot_utils, "uninstall_cpp_java", lambda ssh: True)
    deploy = cli_deploy.Deploy(argparse.ArgumentParser())
    monkeypatch.setattr(deploy, "_do_deploy", lambda *args: True)
    assert (
        deploy.run(
            main_file=main_file,
            project_path=tmp_path,
            robot_class=None,
            builtin=False,
            skip_tests=True,
            debug=False,
            nc=False,
            nc_ds=False,
            ignore_image_version=False,
            no_install=True,
            no_verify=False,
            no_uninstall=False,
            force_install=False,
            large=True,
            robot=None,
            team=None,
            no_resolve=True,
            local=False,
            cache_root=tmp_path,
        )
        == 0
    )
    assert "SUCCESS: Deploy was successful!" in capsys.readouterr().out


def test_deploy_cache_changes_with_runtime_and_root(tmp_path):
    from robotpy_installer.installer import RobotpyInstaller
    from robotpy_installer.python_runtime import PYTHON_314, PYTHON_315

    cache = tmp_path / "pip_cache"
    cache.mkdir()
    (cache / "old-1-cp314-cp314-linux_aarch64.whl").touch()
    (cache / "new-1-cp315-cp315-linux_aarch64.whl").touch()
    deploy = cli_deploy.Deploy(argparse.ArgumentParser())
    installer = RobotpyInstaller(cache_root=tmp_path, python_runtime=PYTHON_314)
    assert set(deploy._get_cached_packages(installer)) == {"old"}
    installer.python_runtime = PYTHON_315
    assert set(deploy._get_cached_packages(installer)) == {"new"}
    installer = RobotpyInstaller(cache_root=tmp_path / "empty")
    assert deploy._get_cached_packages(installer) == {}


@pytest.fixture
def deployment(monkeypatch):
    deploy = cli_deploy.Deploy(argparse.ArgumentParser())
    installer = MagicMock()
    installer.is_python_installed.return_value = True
    installer.get_python_version.return_value = cli_deploy.required_pyversion
    ssh = MagicMock()
    ssh.sftp_remote_file_exists.return_value = True
    monkeypatch.setattr(cli_deploy.robot_utils, "uninstall_cpp_java", lambda ssh: True)
    monkeypatch.setattr(deploy, "_get_robot_packages", lambda ssh: {})
    monkeypatch.setattr(
        deploy,
        "_get_cached_packages",
        lambda installer: pypackages.make_packages(
            {"example": "1.2", "robotpy": f"{YEAR}.1.0"}
        ),
    )
    return deploy, installer, ssh


@pytest.mark.parametrize(
    "pin, python_version", [("2027.0.0a7", (3, 14)), ("2027.0.0a8", (3, 15))]
)
def test_deploy_checks_project_runtime(deployment, pin, python_version):
    deploy, installer, ssh = deployment
    project = pyproject.loads(f'[tool.robotpy]\nrobotpy_version="{pin}"')
    installer.get_python_version.return_value = python_version
    deploy._ensure_requirements(
        project,
        installer,
        ssh,
        no_install=True,
        force_install=False,
        no_uninstall=False,
    )
    installer.install_python.assert_not_called()
    assert installer.python_runtime.version_tuple == python_version


@pytest.mark.parametrize("force_install", [False, True])
@pytest.mark.parametrize("python_state", ["current", "missing", "outdated"])
def test_ignored_empty_requires_never_clears_or_installs_packages(
    deployment, force_install, python_state
):
    deploy, installer, ssh = deployment
    project = pyproject.loads('[tool.robotpy]\nrobotpy_version = "ignored"\n')
    if python_state == "missing":
        installer.is_python_installed.return_value = False
    elif python_state == "outdated":
        installer.get_python_version.return_value = (3, 8)

    deploy._ensure_requirements(
        project,
        installer,
        ssh,
        no_install=False,
        force_install=force_install,
        no_uninstall=False,
        assume_yes=True,
    )

    installer.uninstall_venv.assert_not_called()
    installer.pip_install.assert_not_called()
    if python_state == "current":
        installer.install_python.assert_not_called()
    else:
        installer.install_python.assert_called_once_with()


@pytest.mark.parametrize("force_install", [False, True])
@pytest.mark.parametrize("no_uninstall", [False, True])
@pytest.mark.parametrize(
    "version, packages",
    [
        ("ignored", ["example==1.2"]),
        (f"{YEAR}.1.0", [f"robotpy=={YEAR}.1.0", "example==1.2"]),
    ],
)
def test_nonempty_requires_retains_normal_clearing_behavior(
    deployment, no_uninstall, force_install, version, packages
):
    deploy, installer, ssh = deployment
    project = pyproject.loads(
        f'[tool.robotpy]\nrobotpy_version = "{version}"\nrequires = ["example==1.2"]\n'
    )

    deploy._ensure_requirements(
        project,
        installer,
        ssh,
        no_install=False,
        force_install=force_install,
        no_uninstall=no_uninstall,
        assume_yes=True,
    )

    if no_uninstall:
        installer.uninstall_venv.assert_not_called()
    else:
        installer.uninstall_venv.assert_called_once_with()
    installer.pip_install.assert_called_once_with(
        False, False, False, False, [], packages
    )
