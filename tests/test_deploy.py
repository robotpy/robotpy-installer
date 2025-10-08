"""
Unit tests for the deploy command (cli_deploy.py)
"""

import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch, call
import subprocess

from robotpy_installer.cli_deploy import Deploy, wrap_ssh_error
from robotpy_installer import sshcontroller, pyproject
from robotpy_installer.errors import Error


class TestWrapSshError(unittest.TestCase):
    """Tests for the wrap_ssh_error context manager"""

    def test_wrap_ssh_error_success(self):
        """Test that wrap_ssh_error passes through when no error occurs"""
        with wrap_ssh_error("test operation"):
            pass  # Should complete without error

    def test_wrap_ssh_error_wraps_exception(self):
        """Test that wrap_ssh_error wraps SshExecError with additional context"""
        with self.assertRaises(sshcontroller.SshExecError) as cm:
            with wrap_ssh_error("test operation"):
                raise sshcontroller.SshExecError("original error", 1)

        self.assertIn("test operation", str(cm.exception))
        self.assertIn("original error", str(cm.exception))
        self.assertEqual(cm.exception.retval, 1)


class TestDeploy(unittest.TestCase):
    """Tests for the Deploy class"""

    def setUp(self):
        """Set up test fixtures"""
        self.parser = MagicMock()
        self.deploy = Deploy(self.parser)
        self.temp_dir = tempfile.mkdtemp()
        self.project_path = pathlib.Path(self.temp_dir)
        self.main_file = self.project_path / "robot.py"
        self.main_file.write_text("# test robot")

    def test_parser_arguments(self):
        """Test that all required arguments are added to the parser"""
        # Verify parser.add_argument was called for each command line option
        # Note: parser.add_argument is called, but mutually_exclusive_group also has add_argument
        call_count = self.parser.add_argument.call_count
        if hasattr(self.parser, "add_mutually_exclusive_group"):
            for (
                call
            ) in (
                self.parser.add_mutually_exclusive_group.return_value.add_argument.call_args_list
            ):
                call_count += 1

        self.assertGreater(call_count, 0)

        # Check for key arguments - collect all argument names including both short and long forms
        arg_names = []
        for call_args in self.parser.add_argument.call_args_list:
            arg_names.extend(call_args[0])

        # Also check the mutually exclusive group arguments
        if hasattr(self.parser, "add_mutually_exclusive_group"):
            for (
                call_args
            ) in (
                self.parser.add_mutually_exclusive_group.return_value.add_argument.call_args_list
            ):
                arg_names.extend(call_args[0])

        self.assertIn("--builtin", arg_names)
        self.assertIn("--skip-tests", arg_names)
        self.assertIn("--debug", arg_names)
        self.assertIn("--nc", arg_names)

    def test_init_packages_cache(self):
        """Test that package cache is initialized to None"""
        self.assertIsNone(self.deploy._packages_in_cache)
        self.assertIsNone(self.deploy._robot_packages)

    @patch("robotpy_installer.cli_deploy.subprocess.run")
    def test_run_blocks_home_directory_deploy(self, mock_run):
        """Test that deploying from home directory is blocked"""
        home_file = pathlib.Path.home() / "robot.py"
        home_file.write_text("# test")

        try:
            result = self.deploy.run(
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
            self.assertFalse(result)
        finally:
            home_file.unlink()

    @patch("robotpy_installer.cli_deploy.subprocess.run")
    def test_run_tests_by_default(self, mock_run):
        """Test that tests are run by default when skip_tests=False"""
        mock_run.return_value = Mock(returncode=1)

        with patch.object(self.deploy, "_check_large_files", return_value=True):
            result = self.deploy.run(
                main_file=self.main_file,
                project_path=self.project_path,
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
        self.assertIn("test", call_args)
        self.assertEqual(result, 1)

    @patch("robotpy_installer.cli_deploy.subprocess.run")
    @patch("robotpy_installer.cli_deploy.sshcontroller.ssh_from_cfg")
    @patch("robotpy_installer.cli_deploy.pyproject.load")
    def test_skip_tests_flag(self, mock_load, mock_ssh, mock_run):
        """Test that --skip-tests flag skips test execution"""
        mock_ssh_ctx = MagicMock()
        mock_ssh.__enter__ = Mock(return_value=mock_ssh_ctx)
        mock_ssh.__exit__ = Mock(return_value=False)

        with patch.object(self.deploy, "_check_large_files", return_value=True):
            with patch.object(self.deploy, "_ensure_requirements"):
                with patch.object(self.deploy, "_do_deploy", return_value=True):
                    result = self.deploy.run(
                        main_file=self.main_file,
                        project_path=self.project_path,
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
        self.assertEqual(result, 0)

    def test_check_large_files_allows_small_files(self):
        """Test that small files pass the size check"""
        # Create small test file
        test_file = self.project_path / "small.py"
        test_file.write_text("# small file")

        result = self.deploy._check_large_files(self.project_path)
        self.assertTrue(result)

    @patch("robotpy_installer.cli_deploy.yesno")
    def test_check_large_files_blocks_large_files(self, mock_yesno):
        """Test that large files are blocked without confirmation"""
        mock_yesno.return_value = False

        # Create large test file (> 250k)
        large_file = self.project_path / "large.bin"
        large_file.write_bytes(b"x" * 300000)

        result = self.deploy._check_large_files(self.project_path)
        self.assertFalse(result)
        mock_yesno.assert_called_once()

    @patch("robotpy_installer.cli_deploy.yesno")
    def test_check_large_files_allows_with_confirmation(self, mock_yesno):
        """Test that large files are allowed with user confirmation"""
        mock_yesno.return_value = True

        # Create large test file (> 250k)
        large_file = self.project_path / "large.bin"
        large_file.write_bytes(b"x" * 300000)

        result = self.deploy._check_large_files(self.project_path)
        self.assertTrue(result)
        mock_yesno.assert_called_once()

    def test_generate_build_data_basic(self):
        """Test that build data is generated correctly"""
        build_data = self.deploy._generate_build_data(self.project_path)

        self.assertIn("deploy-host", build_data)
        self.assertIn("deploy-user", build_data)
        self.assertIn("deploy-date", build_data)
        self.assertIn("code-path", build_data)
        self.assertEqual(build_data["code-path"], str(self.project_path))

    @patch("robotpy_installer.cli_deploy.subprocess.run")
    def test_generate_build_data_with_git(self, mock_run):
        """Test that git information is included when in a git repo"""
        # Mock git commands
        mock_run.side_effect = [
            Mock(stdout=b"true\n", returncode=0),  # is-inside-work-tree
            Mock(stdout=b"abc123\n", returncode=0),  # rev-parse HEAD
            Mock(stdout=b"v1.0.0\n", returncode=0),  # describe
            Mock(stdout=b"main\n", returncode=0),  # rev-parse --abbrev-ref HEAD
        ]

        build_data = self.deploy._generate_build_data(self.project_path)

        self.assertIn("git-hash", build_data)
        self.assertIn("git-desc", build_data)
        self.assertIn("git-branch", build_data)
        self.assertEqual(build_data["git-hash"], "abc123")
        self.assertEqual(build_data["git-desc"], "v1.0.0")
        self.assertEqual(build_data["git-branch"], "main")

    def test_copy_to_tmpdir_basic(self):
        """Test that files are copied to temp directory correctly"""
        # Create test files
        (self.project_path / "constants.py").write_text("# constants")

        import shutil

        tmp_dir = pathlib.Path(tempfile.mkdtemp())
        py_dir = tmp_dir / "code"
        try:
            uploaded = self.deploy._copy_to_tmpdir(py_dir, self.project_path)

            # Check that files were identified (robot.py from setUp and constants.py)
            self.assertEqual(len(uploaded), 2)

            # Check that files were copied
            self.assertTrue((py_dir / "robot.py").exists())
            self.assertTrue((py_dir / "constants.py").exists())
        finally:
            shutil.rmtree(tmp_dir)

    def test_copy_to_tmpdir_ignores_hidden_files(self):
        """Test that hidden files and directories are ignored"""
        # Create hidden file and directory
        (self.project_path / ".hidden").write_text("hidden")
        (self.project_path / ".git").mkdir()
        (self.project_path / ".git" / "config").write_text("git config")

        uploaded = self.deploy._copy_to_tmpdir(
            pathlib.Path(), self.project_path, dry_run=True
        )

        # Hidden files should not be in the upload list
        upload_names = [pathlib.Path(f).name for f in uploaded]
        self.assertNotIn(".hidden", upload_names)
        self.assertNotIn("config", upload_names)

    def test_copy_to_tmpdir_ignores_pyc_files(self):
        """Test that .pyc files are ignored"""
        # Create .pyc file
        (self.project_path / "robot.pyc").write_bytes(b"compiled")

        uploaded = self.deploy._copy_to_tmpdir(
            pathlib.Path(), self.project_path, dry_run=True
        )

        # .pyc files should not be in the upload list
        upload_names = [pathlib.Path(f).name for f in uploaded]
        self.assertNotIn("robot.pyc", upload_names)

    def test_copy_to_tmpdir_ignores_wheel_files(self):
        """Test that .whl files are ignored"""
        # Create .whl file
        (self.project_path / "package.whl").write_bytes(b"wheel data")

        uploaded = self.deploy._copy_to_tmpdir(
            pathlib.Path(), self.project_path, dry_run=True
        )

        # .whl files should not be in the upload list
        upload_names = [pathlib.Path(f).name for f in uploaded]
        self.assertNotIn("package.whl", upload_names)

    def test_copy_to_tmpdir_ignores_pycache(self):
        """Test that __pycache__ directories are ignored"""
        # Create __pycache__ directory
        pycache = self.project_path / "__pycache__"
        pycache.mkdir()
        (pycache / "robot.pyc").write_bytes(b"compiled")

        uploaded = self.deploy._copy_to_tmpdir(
            pathlib.Path(), self.project_path, dry_run=True
        )

        # __pycache__ files should not be in the upload list
        upload_paths = [str(f) for f in uploaded]
        self.assertFalse(any("__pycache__" in p for p in upload_paths))

    def test_copy_to_tmpdir_ignores_venv(self):
        """Test that venv directories are ignored"""
        # Create venv directory
        venv = self.project_path / "venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("config")

        uploaded = self.deploy._copy_to_tmpdir(
            pathlib.Path(), self.project_path, dry_run=True
        )

        # venv files should not be in the upload list
        upload_paths = [str(f) for f in uploaded]
        self.assertFalse(any("venv" in p for p in upload_paths))

    @patch("robotpy_installer.cli_deploy.RobotpyInstaller")
    def test_get_cached_packages(self, mock_installer_class):
        """Test that cached packages are retrieved and cached"""
        mock_installer = MagicMock()
        mock_installer.cache_root = pathlib.Path("/cache")

        with patch(
            "robotpy_installer.cli_deploy.pypackages.get_pip_cache_packages"
        ) as mock_get:
            mock_get.return_value = {"robotpy": ("2024.0.0",)}

            # First call should fetch
            result1 = self.deploy._get_cached_packages(mock_installer)
            self.assertEqual(result1, {"robotpy": ("2024.0.0",)})
            mock_get.assert_called_once()

            # Second call should use cache
            result2 = self.deploy._get_cached_packages(mock_installer)
            self.assertEqual(result2, {"robotpy": ("2024.0.0",)})
            # Should still only have been called once
            self.assertEqual(mock_get.call_count, 1)

    def test_get_robot_packages_caches_result(self):
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
                result1 = self.deploy._get_robot_packages(mock_ssh)
                self.assertEqual(result1, {"robotpy": ("2024.0.0",)})
                mock_get.assert_called_once()

                # Second call should use cache
                result2 = self.deploy._get_robot_packages(mock_ssh)
                self.assertEqual(result2, {"robotpy": ("2024.0.0",)})
                # Should still only have been called once
                self.assertEqual(mock_get.call_count, 1)

    @patch("robotpy_installer.cli_deploy.RobotpyInstaller")
    def test_clear_pip_packages(self, mock_installer_class):
        """Test that pip packages are uninstalled correctly"""
        mock_installer = MagicMock()
        self.deploy._robot_packages = {
            "robotpy": ("2024.0.0",),
            "pip": ("23.0",),
            "numpy": ("1.24.0",),
        }

        self.deploy._clear_pip_packages(mock_installer)

        # Should uninstall everything except pip
        mock_installer.pip_uninstall.assert_called_once()
        uninstalled = mock_installer.pip_uninstall.call_args[0][0]
        self.assertIn("robotpy", uninstalled)
        self.assertIn("numpy", uninstalled)
        self.assertNotIn("pip", uninstalled)

        # Cache should be cleared
        self.assertIsNone(self.deploy._packages_in_cache)


class TestDeployIntegration(unittest.TestCase):
    """Integration tests for deploy workflow"""

    @patch("robotpy_installer.cli_deploy.subprocess.run")
    @patch("robotpy_installer.cli_deploy.sshcontroller.ssh_from_cfg")
    @patch("robotpy_installer.cli_deploy.pyproject.load")
    def test_successful_deploy_workflow(self, mock_load, mock_ssh, mock_run):
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
        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = pathlib.Path(temp_dir)
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

            self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
