import argparse
import pathlib
from unittest.mock import MagicMock, patch

from robotpy_installer.cli_installer import (
    InstallerDownload,
    InstallerDownloadPython,
    InstallerInstall,
    InstallerInstallPython,
    InstallerUninstall,
    InstallerUninstallJavaCpp,
    InstallerUninstallPython,
    InstallerUninstallRobotPy,
)
from robotpy_installer.sshcontroller import LocalController


def test_download_python_parser_accepts_cache_root(tmp_path):
    parser = argparse.ArgumentParser()
    InstallerDownloadPython(parser)

    args = parser.parse_args(["--cache-root", str(tmp_path / "cache")])

    assert args.cache_root == tmp_path / "cache"


def test_download_python_uses_requested_cache_root(tmp_path):
    cache_root = tmp_path / "cache"
    cmd = InstallerDownloadPython(argparse.ArgumentParser())
    fake_installer = MagicMock()

    with patch(
        "robotpy_installer.cli_installer.RobotpyInstaller", return_value=fake_installer
    ) as installer_cls:
        cmd.run(use_certifi=False, cache_root=cache_root)

    installer_cls.assert_called_once_with(cache_root=cache_root)
    fake_installer.download_python.assert_called_once_with(False)


def test_download_parser_accepts_cache_root(tmp_path):
    parser = argparse.ArgumentParser()
    InstallerDownload(parser)

    args = parser.parse_args(["--cache-root", str(tmp_path / "cache"), "robotpy"])

    assert args.cache_root == tmp_path / "cache"
    assert args.packages == ["robotpy"]


def test_download_uses_requested_cache_root(tmp_path):
    cache_root = tmp_path / "cache"
    cmd = InstallerDownload(argparse.ArgumentParser())
    fake_installer = MagicMock()
    requirements = (tmp_path / "requirements.txt",)
    packages = ("robotpy",)
    find_links = tmp_path / "links"

    with patch(
        "robotpy_installer.cli_installer.RobotpyInstaller", return_value=fake_installer
    ) as installer_cls:
        cmd.run(
            find_links=find_links,
            no_deps=True,
            pre=False,
            requirements=requirements,
            packages=packages,
            cache_root=cache_root,
        )

    installer_cls.assert_called_once_with(cache_root=cache_root)
    fake_installer.pip_download.assert_called_once_with(
        True, False, requirements, packages, find_links
    )


def test_install_parser_accepts_cache_root(tmp_path):
    parser = argparse.ArgumentParser()
    InstallerInstall(parser)

    args = parser.parse_args(["--cache-root", str(tmp_path / "cache"), "robotpy"])

    assert args.cache_root == tmp_path / "cache"
    assert args.packages == ["robotpy"]


def test_install_uninstall_parsers_accept_local():
    command_classes = [
        InstallerInstall,
        InstallerInstallPython,
        InstallerUninstall,
        InstallerUninstallJavaCpp,
        InstallerUninstallPython,
        InstallerUninstallRobotPy,
    ]

    for command_cls in command_classes:
        parser = argparse.ArgumentParser()
        command_cls(parser)

        args = parser.parse_args(["--local"])

        assert args.local is True


def test_install_uses_requested_cache_root(tmp_path):
    cache_root = tmp_path / "cache"
    cmd = InstallerInstall(argparse.ArgumentParser())
    fake_installer = MagicMock()
    fake_context = fake_installer.connect_to_robot.return_value
    fake_context.__enter__.return_value = None
    fake_context.__exit__.return_value = None

    with patch(
        "robotpy_installer.cli_installer.RobotpyInstaller", return_value=fake_installer
    ) as installer_cls:
        cmd.run(
            project_path=tmp_path,
            main_file=tmp_path / "robot.py",
            ignore_image_version=False,
            robot=None,
            force_reinstall=False,
            ignore_installed=False,
            no_deps=True,
            pre=False,
            requirements=(),
            packages=("robotpy",),
            cache_root=cache_root,
        )

    installer_cls.assert_called_once_with(cache_root=cache_root)
    fake_installer.connect_to_robot.assert_called_once()
    fake_installer.pip_install.assert_called_once_with(
        False, False, True, False, (), ("robotpy",)
    )


def _fake_connected_installer():
    fake_installer = MagicMock()
    fake_context = fake_installer.connect_to_robot.return_value
    fake_context.__enter__.return_value = None
    fake_context.__exit__.return_value = None
    return fake_installer


def _assert_local_connection(fake_installer):
    fake_installer.connect_to_robot.assert_called_once()
    kwargs = fake_installer.connect_to_robot.call_args.kwargs
    assert kwargs["robot_or_team"] is None
    assert isinstance(kwargs["ssh"], LocalController)


def test_install_local_uses_local_controller(tmp_path):
    cmd = InstallerInstall(argparse.ArgumentParser())
    fake_installer = _fake_connected_installer()

    with patch(
        "robotpy_installer.cli_installer.RobotpyInstaller", return_value=fake_installer
    ):
        cmd.run(
            project_path=tmp_path,
            main_file=tmp_path / "robot.py",
            ignore_image_version=False,
            robot="10.0.0.2",
            local=True,
            force_reinstall=False,
            ignore_installed=False,
            no_deps=True,
            pre=False,
            requirements=(),
            packages=("robotpy",),
            cache_root=None,
        )

    _assert_local_connection(fake_installer)
    fake_installer.pip_install.assert_called_once_with(
        False, False, True, False, (), ("robotpy",)
    )


def test_basic_install_uninstall_local_uses_local_controller(tmp_path):
    for command_cls, method_name in [
        (InstallerInstallPython, "install_python"),
        (InstallerUninstallPython, "uninstall_python"),
    ]:
        cmd = command_cls(argparse.ArgumentParser())
        fake_installer = _fake_connected_installer()

        with patch(
            "robotpy_installer.cli_installer.RobotpyInstaller",
            return_value=fake_installer,
        ):
            cmd.run(
                project_path=tmp_path,
                main_file=tmp_path / "robot.py",
                ignore_image_version=False,
                robot="10.0.0.2",
                local=True,
            )

        _assert_local_connection(fake_installer)
        getattr(fake_installer, method_name).assert_called_once_with()


def test_uninstall_robotpy_local_uses_local_controller(tmp_path):
    cmd = InstallerUninstallRobotPy(argparse.ArgumentParser())
    fake_installer = _fake_connected_installer()

    with patch(
        "robotpy_installer.cli_installer.RobotpyInstaller", return_value=fake_installer
    ):
        cmd.run(
            project_path=tmp_path,
            main_file=tmp_path / "robot.py",
            ignore_image_version=False,
            robot="10.0.0.2",
            local=True,
            yes=True,
        )

    _assert_local_connection(fake_installer)
    fake_installer.uninstall_robotpy.assert_called_once_with()


def test_uninstall_package_local_uses_local_controller(tmp_path):
    cmd = InstallerUninstall(argparse.ArgumentParser())
    fake_installer = _fake_connected_installer()

    with patch(
        "robotpy_installer.cli_installer.RobotpyInstaller", return_value=fake_installer
    ):
        cmd.run(
            project_path=tmp_path,
            main_file=tmp_path / "robot.py",
            ignore_image_version=False,
            robot="10.0.0.2",
            local=True,
            packages=["robotpy"],
        )

    _assert_local_connection(fake_installer)
    fake_installer.pip_uninstall.assert_called_once_with(["robotpy"])


def test_uninstall_java_cpp_local_uses_local_controller(tmp_path):
    cmd = InstallerUninstallJavaCpp(argparse.ArgumentParser())
    fake_installer = _fake_connected_installer()

    with (
        patch(
            "robotpy_installer.cli_installer.RobotpyInstaller",
            return_value=fake_installer,
        ),
        patch(
            "robotpy_installer.cli_installer.robot_utils.uninstall_cpp_java"
        ) as uninstall_cpp_java,
    ):
        cmd.run(
            project_path=tmp_path,
            main_file=tmp_path / "robot.py",
            ignore_image_version=False,
            robot="10.0.0.2",
            local=True,
        )

    _assert_local_connection(fake_installer)
    uninstall_cpp_java.assert_called_once_with(fake_installer.ssh)
