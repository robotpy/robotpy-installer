"""
Unit tests for the deploy command (cli_deploy.py)
"""

import pathlib
import tempfile
import shutil
from unittest.mock import MagicMock, Mock, patch
import subprocess
import pytest

from robotpy_installer.cli_deploy import Deploy, wrap_ssh_error
from robotpy_installer import sshcontroller, pyproject
from robotpy_installer.errors import Error


# Tests for the wrap_ssh_error context manager


def test_wrap_ssh_error_success():
    """Test that wrap_ssh_error passes through when no error occurs"""
    with wrap_ssh_error("test operation"):
        pass  # Should complete without error


def test_wrap_ssh_error_wraps_exception():
    """Test that wrap_ssh_error wraps SshExecError with additional context"""
    with pytest.raises(sshcontroller.SshExecError) as exc_info:
        with wrap_ssh_error("test operation"):
            raise sshcontroller.SshExecError("original error", 1)

    assert "test operation" in str(exc_info.value)
    assert "original error" in str(exc_info.value)
    assert exc_info.value.retval == 1


# Tests for the Deploy class


@pytest.fixture
def deploy():
    """Create a Deploy instance for testing"""
    parser = MagicMock()
    return Deploy(parser)


@pytest.fixture
def project_path(tmp_path):
    """Create a temporary project directory with a robot.py file"""
    main_file = tmp_path / "robot.py"
    main_file.write_text("# test robot")
    return tmp_path


def test_parser_arguments():
    """Test that all required arguments are added to the parser"""
    parser = MagicMock()
    Deploy(parser)

    # Verify parser.add_argument was called for each command line option
    call_count = parser.add_argument.call_count
    if hasattr(parser, "add_mutually_exclusive_group"):
        for (
            call
        ) in (
            parser.add_mutually_exclusive_group.return_value.add_argument.call_args_list
        ):
            call_count += 1

    assert call_count > 0

    # Check for key arguments - collect all argument names including both short and long forms
    arg_names = []
    for call_args in parser.add_argument.call_args_list:
        arg_names.extend(call_args[0])

    # Also check the mutually exclusive group arguments
    if hasattr(parser, "add_mutually_exclusive_group"):
        for (
            call_args
        ) in (
            parser.add_mutually_exclusive_group.return_value.add_argument.call_args_list
        ):
            arg_names.extend(call_args[0])

    assert "--builtin" in arg_names
    assert "--skip-tests" in arg_names
    assert "--debug" in arg_names
    assert "--nc" in arg_names


def test_init_packages_cache(deploy):
    """Test that package cache is initialized to None"""
    assert deploy._packages_in_cache is None
    assert deploy._robot_packages is None


@patch("robotpy_installer.cli_deploy.subprocess.run")
def test_run_blocks_home_directory_deploy(mock_run, deploy):
    """Test that deploying from home directory is blocked"""
    home_file = pathlib.Path.home() / "robot.py"
    home_file.write_text("# test")

    try:
        result = deploy.run(
            main_file=home_file,
            project_path=pathlib.Path.home(),
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
            large=False,
            robot="10.0.0.2",
            team=None,
            no_resolve=False,
        )
        assert result is False
    finally:
        home_file.unlink()


@patch("robotpy_installer.cli_deploy.subprocess.run")
def test_run_tests_by_default(mock_run, deploy, project_path):
    """Test that tests are run by default when skip_tests=False"""
    mock_run.return_value = Mock(returncode=1)
    main_file = project_path / "robot.py"

    with patch.object(deploy, "_check_large_files", return_value=True):
        result = deploy.run(
            main_file=main_file,
            project_path=project_path,
            robot_class=None,
            builtin=False,
            skip_tests=False,
            debug=False,
            nc=False,
            nc_ds=False,
            ignore_image_version=False,
            no_install=True,
            no_verify=False,
            no_uninstall=False,
            force_install=False,
            large=False,
            robot="10.0.0.2",
            team=None,
            no_resolve=False,
        )

    # Should have called test command
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "test" in call_args
    assert result == 1


@patch("robotpy_installer.cli_deploy.subprocess.run")
@patch("robotpy_installer.cli_deploy.sshcontroller.ssh_from_cfg")
@patch("robotpy_installer.cli_deploy.pyproject.load")
def test_skip_tests_flag(mock_load, mock_ssh, mock_run, deploy, project_path):
    """Test that --skip-tests flag skips test execution"""
    mock_ssh_ctx = MagicMock()
    mock_ssh.__enter__ = Mock(return_value=mock_ssh_ctx)
    mock_ssh.__exit__ = Mock(return_value=False)
    main_file = project_path / "robot.py"

    with patch.object(deploy, "_check_large_files", return_value=True):
        with patch.object(deploy, "_ensure_requirements"):
            with patch.object(deploy, "_do_deploy", return_value=True):
                result = deploy.run(
                    main_file=main_file,
                    project_path=project_path,
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
                    large=False,
                    robot="10.0.0.2",
                    team=None,
                    no_resolve=False,
                )

    # Test command should not have been called
    mock_run.assert_not_called()
    assert result == 0


def test_check_large_files_allows_small_files(deploy, project_path):
    """Test that small files pass the size check"""
    # Create small test file
    test_file = project_path / "small.py"
    test_file.write_text("# small file")

    result = deploy._check_large_files(project_path)
    assert result is True


@patch("robotpy_installer.cli_deploy.yesno")
def test_check_large_files_blocks_large_files(mock_yesno, deploy, project_path):
    """Test that large files are blocked without confirmation"""
    mock_yesno.return_value = False

    # Create large test file (> 250k)
    large_file = project_path / "large.bin"
    large_file.write_bytes(b"x" * 300000)

    result = deploy._check_large_files(project_path)
    assert result is False
    mock_yesno.assert_called_once()


@patch("robotpy_installer.cli_deploy.yesno")
def test_check_large_files_allows_with_confirmation(mock_yesno, deploy, project_path):
    """Test that large files are allowed with user confirmation"""
    mock_yesno.return_value = True

    # Create large test file (> 250k)
    large_file = project_path / "large.bin"
    large_file.write_bytes(b"x" * 300000)

    result = deploy._check_large_files(project_path)
    assert result is True
    mock_yesno.assert_called_once()


def test_generate_build_data_basic(deploy, project_path):
    """Test that build data is generated correctly"""
    build_data = deploy._generate_build_data(project_path)

    assert "deploy-host" in build_data
    assert "deploy-user" in build_data
    assert "deploy-date" in build_data
    assert "code-path" in build_data
    assert build_data["code-path"] == str(project_path)


@patch("robotpy_installer.cli_deploy.subprocess.run")
def test_generate_build_data_with_git(mock_run, deploy, project_path):
    """Test that git information is included when in a git repo"""
    # Mock git commands
    mock_run.side_effect = [
        Mock(stdout=b"true\n", returncode=0),  # is-inside-work-tree
        Mock(stdout=b"abc123\n", returncode=0),  # rev-parse HEAD
        Mock(stdout=b"v1.0.0\n", returncode=0),  # describe
        Mock(stdout=b"main\n", returncode=0),  # rev-parse --abbrev-ref HEAD
    ]

    build_data = deploy._generate_build_data(project_path)

    assert "git-hash" in build_data
    assert "git-desc" in build_data
    assert "git-branch" in build_data
    assert build_data["git-hash"] == "abc123"
    assert build_data["git-desc"] == "v1.0.0"
    assert build_data["git-branch"] == "main"


def test_copy_to_tmpdir_basic(deploy, project_path):
    """Test that files are copied to temp directory correctly"""
    # Create test files
    (project_path / "constants.py").write_text("# constants")

    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    py_dir = tmp_dir / "code"
    try:
        uploaded = deploy._copy_to_tmpdir(py_dir, project_path)

        # Check that files were identified (robot.py from fixture and constants.py)
        assert len(uploaded) == 2

        # Check that files were copied
        assert (py_dir / "robot.py").exists()
        assert (py_dir / "constants.py").exists()
    finally:
        shutil.rmtree(tmp_dir)


def test_copy_to_tmpdir_ignores_hidden_files(deploy, project_path):
    """Test that hidden files and directories are ignored"""
    # Create hidden file and directory
    (project_path / ".hidden").write_text("hidden")
    (project_path / ".git").mkdir()
    (project_path / ".git" / "config").write_text("git config")

    uploaded = deploy._copy_to_tmpdir(pathlib.Path(), project_path, dry_run=True)

    # Hidden files should not be in the upload list
    upload_names = [pathlib.Path(f).name for f in uploaded]
    assert ".hidden" not in upload_names
    assert "config" not in upload_names


def test_copy_to_tmpdir_ignores_pyc_files(deploy, project_path):
    """Test that .pyc files are ignored"""
    # Create .pyc file
    (project_path / "robot.pyc").write_bytes(b"compiled")

    uploaded = deploy._copy_to_tmpdir(pathlib.Path(), project_path, dry_run=True)

    # .pyc files should not be in the upload list
    upload_names = [pathlib.Path(f).name for f in uploaded]
    assert "robot.pyc" not in upload_names


def test_copy_to_tmpdir_ignores_wheel_files(deploy, project_path):
    """Test that .whl files are ignored"""
    # Create .whl file
    (project_path / "package.whl").write_bytes(b"wheel data")

    uploaded = deploy._copy_to_tmpdir(pathlib.Path(), project_path, dry_run=True)

    # .whl files should not be in the upload list
    upload_names = [pathlib.Path(f).name for f in uploaded]
    assert "package.whl" not in upload_names


def test_copy_to_tmpdir_ignores_pycache(deploy, project_path):
    """Test that __pycache__ directories are ignored"""
    # Create __pycache__ directory
    pycache = project_path / "__pycache__"
    pycache.mkdir()
    (pycache / "robot.pyc").write_bytes(b"compiled")

    uploaded = deploy._copy_to_tmpdir(pathlib.Path(), project_path, dry_run=True)

    # __pycache__ files should not be in the upload list
    upload_paths = [str(f) for f in uploaded]
    assert not any("__pycache__" in p for p in upload_paths)


def test_copy_to_tmpdir_ignores_venv(deploy, project_path):
    """Test that venv directories are ignored"""
    # Create venv directory
    venv = project_path / "venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("config")

    uploaded = deploy._copy_to_tmpdir(pathlib.Path(), project_path, dry_run=True)

    # venv files should not be in the upload list
    upload_paths = [str(f) for f in uploaded]
    assert not any("venv" in p for p in upload_paths)


@patch("robotpy_installer.cli_deploy.RobotpyInstaller")
def test_get_cached_packages(mock_installer_class, deploy):
    """Test that cached packages are retrieved and cached"""
    mock_installer = MagicMock()
    mock_installer.cache_root = pathlib.Path("/cache")

    with patch(
        "robotpy_installer.cli_deploy.pypackages.get_pip_cache_packages"
    ) as mock_get:
        mock_get.return_value = {"robotpy": ("2024.0.0",)}

        # First call should fetch
        result1 = deploy._get_cached_packages(mock_installer)
        assert result1 == {"robotpy": ("2024.0.0",)}
        mock_get.assert_called_once()

        # Second call should use cache
        result2 = deploy._get_cached_packages(mock_installer)
        assert result2 == {"robotpy": ("2024.0.0",)}
        # Should still only have been called once
        assert mock_get.call_count == 1


def test_get_robot_packages_caches_result(deploy):
    """Test that robot packages are cached after first retrieval"""
    mock_ssh = MagicMock()

    with patch(
        "robotpy_installer.cli_deploy.roborio_utils.get_rio_py_packages"
    ) as mock_get:
        with patch(
            "robotpy_installer.cli_deploy.pypackages.make_packages"
        ) as mock_make:
            mock_get.return_value = [("robotpy", "2024.0.0")]
            mock_make.return_value = {"robotpy": ("2024.0.0",)}

            # First call should fetch
            result1 = deploy._get_robot_packages(mock_ssh)
            assert result1 == {"robotpy": ("2024.0.0",)}
            mock_get.assert_called_once()

            # Second call should use cache
            result2 = deploy._get_robot_packages(mock_ssh)
            assert result2 == {"robotpy": ("2024.0.0",)}
            # Should still only have been called once
            assert mock_get.call_count == 1


@patch("robotpy_installer.cli_deploy.RobotpyInstaller")
def test_clear_pip_packages(mock_installer_class, deploy):
    """Test that pip packages are uninstalled correctly"""
    mock_installer = MagicMock()
    deploy._robot_packages = {
        "robotpy": ("2024.0.0",),
        "pip": ("23.0",),
        "numpy": ("1.24.0",),
    }

    deploy._clear_pip_packages(mock_installer)

    # Should uninstall everything except pip
    mock_installer.pip_uninstall.assert_called_once()
    uninstalled = mock_installer.pip_uninstall.call_args[0][0]
    assert "robotpy" in uninstalled
    assert "numpy" in uninstalled
    assert "pip" not in uninstalled

    # Cache should be cleared
    assert deploy._packages_in_cache is None


# Integration tests for deploy workflow


@patch("robotpy_installer.cli_deploy.subprocess.run")
@patch("robotpy_installer.cli_deploy.sshcontroller.ssh_from_cfg")
@patch("robotpy_installer.cli_deploy.pyproject.load")
def test_successful_deploy_workflow(mock_load, mock_ssh, mock_run, tmp_path):
    """Test a complete successful deploy workflow"""
    # Set up mocks
    mock_project = MagicMock()
    mock_project.get_install_list.return_value = []
    mock_load.return_value = mock_project

    mock_ssh_instance = MagicMock()
    mock_ssh.__enter__ = Mock(return_value=mock_ssh_instance)
    mock_ssh.__exit__ = Mock(return_value=False)

    # Create deploy instance
    parser = MagicMock()
    deploy = Deploy(parser)

    # Create test project
    project_path = tmp_path
    main_file = project_path / "robot.py"
    main_file.write_text("# robot code")

    with patch.object(deploy, "_check_large_files", return_value=True):
        with patch.object(deploy, "_ensure_requirements"):
            with patch.object(deploy, "_do_deploy", return_value=True):
                result = deploy.run(
                    main_file=main_file,
                    project_path=project_path,
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
                    large=False,
                    robot="10.0.0.2",
                    team=None,
                    no_resolve=False,
                )

    assert result == 0
