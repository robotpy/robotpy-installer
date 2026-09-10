import argparse
from unittest.mock import MagicMock

import pytest

from robotpy_installer import cli_deploy, pyproject, pypackages
from robotpy_installer.installer import _WPILIB_YEAR as YEAR


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
