import inspect
import os
import pathlib
import sys

import pytest

from robotpy_installer.installer import RobotpyInstaller, _WPILIB_YEAR


def test_installer_default_cache_root_is_wpilib_home():
    installer = RobotpyInstaller(log_startup=False)

    assert (
        installer.cache_root
        == pathlib.Path.home() / "wpilib" / _WPILIB_YEAR / "robotpy"
    )
    assert installer.pip_cache == installer.cache_root / "pip_cache"
    assert installer.pkg_cache == installer.cache_root / "pkg_cache"


def test_installer_cache_root_can_be_overridden(tmp_path):
    cache_root = tmp_path / "cache"

    installer = RobotpyInstaller(log_startup=False, cache_root=cache_root)

    assert installer.cache_root == cache_root
    assert installer.pip_cache == cache_root / "pip_cache"
    assert installer.pkg_cache == cache_root / "pkg_cache"


import io

from robotpy_installer.errors import SshExecError
from robotpy_installer.sshcontroller import LocalController

skip_on_windows = pytest.mark.skipif(
    sys.platform == "win32", reason="LocalController executes POSIX SystemCore commands"
)


@skip_on_windows
def test_local_controller_exec_cmd_captures_output():
    with LocalController() as controller:
        result = controller.exec_cmd("printf hello", check=True, get_output=True)

    assert result.returncode == 0
    assert result.stdout == "hello"


@skip_on_windows
def test_local_controller_exec_cmd_raises_on_check_failure():
    with LocalController() as controller:
        try:
            controller.exec_cmd("exit 7", check=True)
        except SshExecError as e:
            assert e.retval == 7
        else:
            raise AssertionError("expected SshExecError")


@skip_on_windows
def test_local_controller_exec_cmd_uses_minimal_environment(monkeypatch):
    monkeypatch.setenv("ROBOTPY_SHOULD_NOT_LEAK", "bad")
    monkeypatch.setenv("HOME", "/tmp/robotpy-home")
    monkeypatch.setenv("PATH", "/tmp/should-not-leak")

    with LocalController() as controller:
        result = controller.exec_cmd(
            'printf \'%s:%s:%s\' "$HOME" "$PATH" "$ROBOTPY_SHOULD_NOT_LEAK"',
            check=True,
            get_output=True,
        )

    assert result.stdout == "/tmp/robotpy-home:/bin:/sbin:/usr/bin:/usr/sbin:"


@skip_on_windows
def test_local_controller_sftp_copies_directory(tmp_path):
    source_root = tmp_path / "source"
    source_child = source_root / "py_new"
    source_child.mkdir(parents=True)
    (source_child / "robot.py").write_text("print('robot')")
    destination = tmp_path / "dest"

    with LocalController() as controller:
        controller.sftp(source_child, destination, mkdir=True)

    assert (destination / "py_new" / "robot.py").read_text() == "print('robot')"


@skip_on_windows
def test_local_controller_sftp_fp_writes_file(tmp_path):
    destination = tmp_path / "nested" / "file.txt"

    with LocalController() as controller:
        controller.sftp_fp(io.BytesIO(b"data"), str(destination))

    assert destination.read_bytes() == b"data"
    assert controller.sftp_remote_file_exists(str(destination))


import urllib.request

from robotpy_installer.cacheserver import CacheServer


def test_cache_server_serves_local_controller_files(tmp_path):
    cache_root = tmp_path / "cache"
    pip_cache = cache_root / "pip_cache"
    pip_cache.mkdir(parents=True)
    (pip_cache / "example.txt").write_text("cached")

    server = CacheServer(LocalController(), cache_root)
    server.start()

    with urllib.request.urlopen(
        f"http://localhost:{server.port}/pip_cache/example.txt"
    ) as fp:
        assert fp.read() == b"cached"

    server.close()


import argparse
from unittest.mock import MagicMock, patch

from robotpy_installer.cli_deploy import Deploy, LocalDeploy, required_pyversion
from robotpy_installer.cli_installer import Installer
from robotpy_installer.installer import _ROBOT_VENV


def _make_deploy_parser():
    parser = argparse.ArgumentParser()
    Deploy(parser)
    return parser


def test_deploy_parser_accepts_local_and_cache_root(tmp_path):
    parser = _make_deploy_parser()

    args = parser.parse_args(["--local", "--cache-root", str(tmp_path / "cache")])

    assert args.local is True
    assert args.cache_root == tmp_path / "cache"


def test_local_deploy_parser_has_no_robot_or_test_options(tmp_path):
    parser = argparse.ArgumentParser()
    LocalDeploy(parser)

    args = parser.parse_args(["--cache-root", str(tmp_path / "cache")])

    assert args.cache_root == tmp_path / "cache"
    assert not hasattr(args, "local")
    assert not hasattr(args, "robot")
    assert not hasattr(args, "team")
    assert not hasattr(args, "skip_tests")
    assert not hasattr(args, "builtin")
    assert not hasattr(args, "nc")
    assert not hasattr(args, "nc_ds")
    assert not hasattr(args, "no_resolve")
    assert not hasattr(args, "no_verify")


def test_local_deploy_installer_subcommand_is_registered():
    assert ("local-deploy", LocalDeploy) in Installer.subcommands


def test_local_deploy_parser_has_yes_option():
    parser = argparse.ArgumentParser()
    LocalDeploy(parser)

    args = parser.parse_args([])
    assert args.blocks is False

    args = parser.parse_args(["--yes"])
    assert args.yes is True


def test_local_deploy_yes_does_not_prompt_on_requirements_mismatch(tmp_path):
    deploy = LocalDeploy(argparse.ArgumentParser())
    main_file = tmp_path / "robot.py"
    main_file.write_text("print('robot')")

    fake_project = MagicMock()
    fake_project.get_install_list.return_value = ["robotpy"]
    fake_project.are_requirements_met.side_effect = [
        (False, ["robotpy missing"]),
        (True, []),
        (True, []),
    ]
    fake_project.get_deploy_list.return_value = ["robotpy"]

    fake_installer = MagicMock()
    fake_installer.is_python_installed.return_value = True
    fake_installer.get_python_version.return_value = required_pyversion
    fake_installer.connect_to_robot.return_value.__enter__.return_value = (
        LocalController()
    )
    fake_installer.connect_to_robot.return_value.__exit__.return_value = None

    with (
        patch("robotpy_installer.cli_deploy.pyproject.load", return_value=fake_project),
        patch(
            "robotpy_installer.cli_deploy.RobotpyInstaller", return_value=fake_installer
        ),
        patch.object(deploy, "_get_robot_packages", return_value={}),
        patch.object(deploy, "_get_cached_packages", return_value={}),
        patch.object(deploy, "_do_deploy", return_value=True),
        patch(
            "robotpy_installer.cli_deploy.robot_utils.uninstall_cpp_java",
            return_value=True,
        ),
        patch("robotpy_installer.cli_deploy.yesno") as fake_yesno,
    ):
        result = deploy.run(
            main_file=main_file,
            project_path=tmp_path,
            debug=False,
            ignore_image_version=False,
            no_install=False,
            no_uninstall=False,
            force_install=False,
            large=False,
            cache_root=None,
            yes=True,
        )

    assert result == 0
    fake_yesno.assert_not_called()
    fake_installer.pip_install.assert_called_once_with(
        False, False, False, False, [], ["robotpy"]
    )


def test_deploy_run_has_no_suppress_no_verify_warning_argument():
    assert "suppress_no_verify_warning" not in inspect.signature(Deploy.run).parameters


def test_local_deploy_forces_no_verify_without_warning(tmp_path):
    deploy = LocalDeploy(argparse.ArgumentParser())
    main_file = tmp_path / "robot.py"
    main_file.write_text("print('robot')")

    fake_project = MagicMock()
    fake_project.get_install_list.return_value = ["robotpy"]
    fake_installer = MagicMock()
    fake_installer.connect_to_robot.return_value.__enter__.return_value = (
        LocalController()
    )
    fake_installer.connect_to_robot.return_value.__exit__.return_value = None

    with (
        patch("robotpy_installer.cli_deploy.pyproject.load", return_value=fake_project),
        patch(
            "robotpy_installer.cli_deploy.RobotpyInstaller", return_value=fake_installer
        ),
        patch.object(deploy, "_check_large_files", return_value=True),
        patch.object(deploy, "_ensure_requirements"),
        patch.object(deploy, "_do_deploy", return_value=True),
        patch("robotpy_installer.cli_deploy.logger.warning") as warning,
    ):
        result = deploy.run(
            main_file=main_file,
            project_path=tmp_path,
            debug=False,
            ignore_image_version=False,
            no_install=False,
            no_uninstall=False,
            force_install=False,
            large=False,
            cache_root=None,
        )

    assert result == 0
    fake_project.are_local_requirements_met.assert_not_called()
    warning.assert_not_called()


def test_local_deploy_runs_local_without_tests_or_robot_class(tmp_path):
    deploy = LocalDeploy(argparse.ArgumentParser())
    main_file = tmp_path / "robot.py"
    main_file.write_text("print('robot')")

    fake_installer = MagicMock()
    fake_installer.connect_to_robot.return_value.__enter__.return_value = (
        LocalController()
    )
    fake_installer.connect_to_robot.return_value.__exit__.return_value = None

    with patch(
        "robotpy_installer.cli_deploy.RobotpyInstaller", return_value=fake_installer
    ) as installer_cls:
        with (
            patch.object(deploy, "_check_large_files", return_value=True),
            patch.object(deploy, "_ensure_requirements"),
            patch.object(deploy, "_do_deploy", return_value=True),
            patch("robotpy_installer.cli_deploy.subprocess.run") as subprocess_run,
        ):
            result = deploy.run(
                main_file=main_file,
                project_path=tmp_path,
                debug=False,
                ignore_image_version=False,
                no_install=True,
                no_uninstall=False,
                force_install=False,
                large=False,
                cache_root=None,
            )

    assert result == 0
    subprocess_run.assert_not_called()
    installer_cls.assert_called_once_with(cache_root=pathlib.Path("/opt/blocks/cache"))
    fake_installer.connect_to_robot.assert_called_once()
    assert fake_installer.connect_to_robot.call_args.kwargs["robot_or_team"] is None
    assert isinstance(
        fake_installer.connect_to_robot.call_args.kwargs["ssh"], LocalController
    )


def test_ensure_requirements_does_not_clear_packages_when_venv_missing():
    deploy = Deploy(argparse.ArgumentParser())
    fake_project = MagicMock()
    fake_project.are_requirements_met.side_effect = [
        (False, ["robotpy missing"]),
        (True, []),
        (True, []),
    ]
    fake_project.get_install_list.return_value = ["robotpy"]
    fake_project.get_deploy_list.return_value = ["robotpy"]

    fake_installer = MagicMock()
    fake_installer.is_python_installed.return_value = True
    fake_installer.get_python_version.return_value = required_pyversion

    fake_ssh = MagicMock()
    fake_ssh.sftp_remote_file_exists.return_value = False

    with (
        patch(
            "robotpy_installer.cli_deploy.robot_utils.uninstall_cpp_java",
            return_value=True,
        ),
        patch("robotpy_installer.cli_deploy.robot_utils.kill_robot_cmd", "true"),
        patch("robotpy_installer.cli_deploy.yesno", return_value=True),
        patch.object(deploy, "_get_robot_packages", return_value={}),
        patch.object(deploy, "_get_cached_packages", return_value={}),
        patch.object(deploy, "_clear_pip_packages") as clear_pip_packages,
        patch("robotpy_installer.cli_deploy.logger.info") as info,
    ):
        deploy._ensure_requirements(
            fake_project,
            fake_installer,
            fake_ssh,
            no_install=False,
            force_install=False,
            no_uninstall=False,
        )

    fake_ssh.sftp_remote_file_exists.assert_any_call(_ROBOT_VENV)
    clear_pip_packages.assert_not_called()
    assert not any(
        call.args
        and call.args[0]
        == "Clearing existing packages on robot before install (specify --no-uninstall to not do this)"
        for call in info.call_args_list
    )
    fake_installer.pip_install.assert_called_once_with(
        False, False, False, False, [], ["robotpy"]
    )


def test_deploy_local_uses_default_blocks_cache(tmp_path):
    deploy = Deploy(argparse.ArgumentParser())
    main_file = tmp_path / "robot.py"
    main_file.write_text("print('robot')")

    fake_installer = MagicMock()
    fake_installer.connect_to_robot.return_value.__enter__.return_value = (
        LocalController()
    )
    fake_installer.connect_to_robot.return_value.__exit__.return_value = None

    with patch(
        "robotpy_installer.cli_deploy.RobotpyInstaller", return_value=fake_installer
    ) as installer_cls:
        with (
            patch.object(deploy, "_check_large_files", return_value=True),
            patch.object(deploy, "_ensure_requirements"),
            patch.object(deploy, "_do_deploy", return_value=True),
        ):
            result = deploy.run(
                main_file=main_file,
                project_path=tmp_path,
                robot_class=object,
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
                large=False,
                robot=None,
                team=None,
                no_resolve=False,
                local=True,
                cache_root=None,
            )

    assert result == 0
    installer_cls.assert_called_once_with(cache_root=pathlib.Path("/opt/blocks/cache"))
    fake_installer.connect_to_robot.assert_called_once()
    assert isinstance(
        fake_installer.connect_to_robot.call_args.kwargs["ssh"], LocalController
    )


def test_deploy_local_uses_requested_cache_root(tmp_path):
    deploy = Deploy(argparse.ArgumentParser())
    main_file = tmp_path / "robot.py"
    main_file.write_text("print('robot')")
    cache_root = tmp_path / "cache"

    fake_installer = MagicMock()
    fake_installer.connect_to_robot.return_value.__enter__.return_value = (
        LocalController()
    )
    fake_installer.connect_to_robot.return_value.__exit__.return_value = None

    with patch(
        "robotpy_installer.cli_deploy.RobotpyInstaller", return_value=fake_installer
    ) as installer_cls:
        with (
            patch.object(deploy, "_check_large_files", return_value=True),
            patch.object(deploy, "_ensure_requirements"),
            patch.object(deploy, "_do_deploy", return_value=True),
        ):
            result = deploy.run(
                main_file=main_file,
                project_path=tmp_path,
                robot_class=object,
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
                large=False,
                robot=None,
                team=None,
                no_resolve=False,
                local=True,
                cache_root=cache_root,
            )

    assert result == 0
    installer_cls.assert_called_once_with(cache_root=cache_root)
