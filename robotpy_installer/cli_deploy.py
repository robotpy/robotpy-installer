import argparse
import contextlib
import datetime
import getpass
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import typing

from os.path import join, splitext

from . import pypackages, pyproject, robot_utils, sshcontroller
from .installer import PipInstallError, PythonMissingError, RobotpyInstaller
from .installer import _ROBOTPY_PYTHON_VERSION_TUPLE as required_pyversion
from .errors import Error
from .utils import handle_cli_error, print_err, yesno

import logging

logger = logging.getLogger("deploy")


@contextlib.contextmanager
def wrap_ssh_error(msg: str):
    try:
        yield
    except sshcontroller.SshExecError as e:
        raise sshcontroller.SshExecError(f"{msg}: {str(e)}", e.retval) from e


class Deploy:
    """
    Installs requirements and uploads code to the robot and executes it immediately

    You must run the 'sync' command first to download the requirements specified
    in pyproject.toml. See `robotpy sync --help` for more details.
    """

    def __init__(self, parser: argparse.ArgumentParser):
        parser.add_argument(
            "--builtin",
            default=False,
            action="store_true",
            help="Use pyfrc's builtin tests if no tests are specified",
        )

        parser.add_argument(
            "--skip-tests",
            action="store_true",
            default=False,
            help="If specified, don't run tests before uploading code to robot (DANGEROUS)",
        )

        parser.add_argument(
            "--debug",
            action="store_true",
            default=False,
            help="If specified, runs the code in debug mode (which only currently enables verbose logging)",
        )

        parser.add_argument(
            "--nc",
            "--netconsole",
            action="store_true",
            default=False,
            help="Attach netconsole listener and show robot stdout (requires DS to be connected)",
        )

        parser.add_argument(
            "--nc-ds",
            "--netconsole-ds",
            action="store_true",
            default=False,
            help="Attach netconsole listener and show robot stdout (fakes a DS connection)",
        )

        parser.add_argument(
            "--ignore-image-version",
            action="store_true",
            default=False,
            help="Ignore RoboRIO image version",
        )

        parser.add_argument(
            "-n",
            "--no-verify",
            action="store_true",
            default=False,
            help="If specified, do not verify that the robotpy version in pyproject.toml is installed locally",
        )

        install_args = parser.add_mutually_exclusive_group()

        install_args.add_argument(
            "--no-install",
            action="store_true",
            default=False,
            help="If specified, do not use pyproject.toml to install packages on the robot before deploy",
        )

        install_args.add_argument(
            "--force-install",
            action="store_true",
            default=False,
            help="Force installation of packages required by pyproject.toml",
        )

        parser.add_argument(
            "--no-uninstall",
            action="store_true",
            default=False,
            help="Do not uninstall packages from the RoboRIO",
        )

        parser.add_argument(
            "--large",
            action="store_true",
            default=False,
            help="If specified, allow uploading large files (> 250k) to the RoboRIO",
        )

        robot_args = parser.add_mutually_exclusive_group()

        robot_args.add_argument(
            "--robot", default=None, help="Set hostname or IP address of robot"
        )

        robot_args.add_argument(
            "--team", default=None, type=int, help="Set team number to deploy robot for"
        )

        parser.add_argument(
            "--no-resolve",
            action="store_true",
            default=False,
            help="If specified, don't do a DNS lookup, allow ssh et al to do it instead",
        )

        self._packages_in_cache: typing.Optional[pypackages.Packages] = None
        self._robot_packages: typing.Optional[pypackages.Packages] = None

    @handle_cli_error
    def run(
        self,
        main_file: pathlib.Path,
        project_path: pathlib.Path,
        robot_class,  # we don't use this but it ensures the code can import locally
        builtin: bool,
        skip_tests: bool,
        debug: bool,
        nc: bool,
        nc_ds: bool,
        ignore_image_version: bool,
        no_install: bool,
        no_verify: bool,
        no_uninstall: bool,
        force_install: bool,
        large: bool,
        robot: typing.Optional[str],
        team: typing.Optional[int],
        no_resolve: bool,
    ):
        # run the test suite before uploading
        if not skip_tests:
            test_args = [
                sys.executable,
                "-m",
                "robotpy",
                "--main",
                str(main_file),
                "test",
            ]
            if builtin:
                test_args.append("--builtin")

            logger.info("Running tests: %s", " ".join(test_args))
            proc = subprocess.run(test_args)
            retval = proc.returncode
            if retval != 0:
                print_err("ERROR: Your robot tests failed, aborting upload.")
                if not sys.stdin.isatty():
                    print_err("- Use --skip-tests if you want to upload anyways")
                    return retval

                print()
                if not yesno("- Upload anyways?"):
                    return retval

                if not yesno("- Are you sure? Your robot code may crash!"):
                    return retval

                print()
                print("WARNING: Uploading code against my better judgement...")

        # upload all files in the robot.py source directory

        robot_filename = main_file.name

        if not large and not self._check_large_files(project_path):
            return 1

        project = None

        if not no_install:
            try:
                project = pyproject.load(project_path, default_if_missing=True)
            except pyproject.NoRobotpyError as e:
                raise pyproject.NoRobotpyError(
                    f"{e}\n\nUse --no-install to ignore this error (not recommended)"
                )

            logger.info("Robot project requirements:")
            for package in project.get_install_list():
                logger.info("- %s", package)

            if no_verify:
                logger.warning("Not checking to see if they are installed on RoboRIO")
            else:
                requirements_met, desc = project.are_local_requirements_met()
                if not requirements_met:
                    logger.warning(
                        "The following project requirements were not installed locally:"
                    )
                    for msg in desc:
                        logger.warning("- %s", msg)

                    msg = (
                        f"Locally installed packages do not match requirements in pyproject.toml (see above)\n"
                        "- If pyproject.toml has older versions, update it to newer versions\n"
                        "- If you have missing packages or older versions installed locally, use\n"
                        "  'python -m robotpy sync' to update your local install\n"
                        "- You can also specify --no-verify to ignore this error (not recommended)"
                    )
                    raise Error(msg)

        with sshcontroller.ssh_from_cfg(
            project_path,
            main_file,
            username="lvuser",
            password="",
            robot_or_team=robot or team,
            no_resolve=no_resolve,
        ) as ssh:
            self._ensure_requirements(
                project,
                project_path,
                main_file,
                ssh,
                ignore_image_version,
                no_install,
                force_install,
                no_uninstall,
            )

            if not self._do_deploy(ssh, debug, nc, nc_ds, robot_filename, project_path):
                return 1

        print("\nSUCCESS: Deploy was successful!")
        return 0

    def _generate_build_data(self, project_path: pathlib.Path) -> dict:
        """
        Generate a deploy.json
        """

        deploy_data = {
            "deploy-host": socket.gethostname(),  # os.uname doesn't work on systems that use non-unix os
            "deploy-user": getpass.getuser(),
            "deploy-date": datetime.datetime.now().replace(microsecond=0).isoformat(),
            "code-path": str(project_path),
        }

        # Test if we're in a git repo or not
        try:
            revParseProcess = subprocess.run(
                args=["git", "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
            )
            in_git_repo = revParseProcess.stdout.decode().strip() == "true"
        except FileNotFoundError:
            in_git_repo = False

        # If we're in a git repo
        if in_git_repo:
            try:
                hashProc = subprocess.run(
                    args=["git", "rev-parse", "HEAD"], capture_output=True
                )

                # Describe this repo
                descProc = subprocess.run(
                    args=["git", "describe", "--dirty=-dirty", "--always"],
                    capture_output=True,
                )

                # Get the branch name
                nameProc = subprocess.run(
                    args=["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True,
                )

                # Insert this data into our deploy.json dict
                deploy_data["git-hash"] = hashProc.stdout.decode().strip()
                deploy_data["git-desc"] = descProc.stdout.decode().strip()
                deploy_data["git-branch"] = nameProc.stdout.decode().strip()
            except subprocess.CalledProcessError as e:
                logging.exception(e)
        else:
            logging.info("Not including git hash in deploy.json: Not a git repo.")

        return deploy_data

    def _check_large_files(self, robot_path: pathlib.Path):
        large_sz = 250000

        large_files = []
        for fname in self._copy_to_tmpdir(pathlib.Path(), robot_path, dry_run=True):
            st = os.stat(fname)
            if st.st_size > large_sz:
                large_files.append((fname, st.st_size))

        if large_files:
            print_err(f"ERROR: large files found (larger than {large_sz} bytes)")
            for fname, sz in sorted(large_files):
                print_err(f"- {fname} ({sz} bytes)")

            if not yesno("Upload anyways?"):
                return False

        return True

    def _get_cached_packages(self, installer: RobotpyInstaller) -> pypackages.Packages:
        if self._packages_in_cache is None:
            self._packages_in_cache = pypackages.get_pip_cache_packages(
                installer.cache_root
            )
        return self._packages_in_cache

    def _get_robot_packages(
        self, ssh: sshcontroller.SshController
    ) -> pypackages.Packages:
        if self._robot_packages is None:
            rio_packages = robot_utils.get_rio_py_packages(ssh)
            self._robot_packages = pypackages.make_packages(rio_packages)
        return self._robot_packages

    def _clear_pip_packages(self, installer: RobotpyInstaller):
        rio_packages = self._get_robot_packages(installer.ssh)
        to_uninstall = [p for p in rio_packages.keys() if p != "pip"]
        if to_uninstall:
            installer.pip_uninstall(to_uninstall)

        self._packages_in_cache = None

    def _ensure_requirements(
        self,
        project: typing.Optional[pyproject.RobotPyProjectToml],
        project_path: pathlib.Path,
        main_file: pathlib.Path,
        ssh: sshcontroller.SshController,
        ignore_image_version: bool,
        no_install: bool,
        force_install: bool,
        no_uninstall: bool,
    ):
        python_exists = False
        python_invalid: typing.Union[bool, str] = False
        requirements_installed = False

        installer = RobotpyInstaller()

        # Has the kill script been updated
        with wrap_ssh_error("checking kill script"):
            kill_script_updated = robot_utils.check_kill_script(ssh)
            if not kill_script_updated:
                logger.warning("Need to update frcKillRobot.sh")

        # does c++/java exist
        with wrap_ssh_error("removing c++/java user programs"):
            cpp_java_exists = not robot_utils.uninstall_cpp_java_lvuser(ssh)

        # does python exist
        with wrap_ssh_error("checking if python exists"):
            python_exists = (
                ssh.exec_cmd("[ -x /usr/local/bin/python3 ]").returncode == 0
            )
            if not python_exists:
                logger.warning("Python is not installed on RoboRIO")

        if python_exists:
            with wrap_ssh_error("getting python version"):
                python_version = robot_utils.get_python3_version(ssh)

            if python_version != required_pyversion:
                python_exists = False
                m, mn = python_version
                rm, rmn = required_pyversion
                python_invalid = f"python{m}{mn}"

                if no_install:
                    raise Error(
                        f"Unsupported version of python ({m}.{mn}) was found on the roboRIO\n"
                        "- could not update it because no-install was specified\n"
                    )

                # Warn the user before changing their rio
                print(
                    "\n"
                    f"Deployer has detected that the version of Python installed on the RoboRIO ({m}.{mn})\n"
                    "is not supported by this installer. The installer will now uninstall that\n"
                    f"and install Python {rm}.{rmn}.\n"
                )

                if not yesno("Reinstall Python"):
                    raise Error("User declined reinstallation")

        if python_exists:
            if no_install:
                requirements_installed = True
            elif not force_install:
                pkgdata = self._get_robot_packages(ssh)

                logger.debug("Roborio has these packages installed:")
                for pkg, version in pkgdata.items():
                    logger.debug("- %s (%s)", pkg, version[0])

                assert project is not None
                requirements_installed, desc = project.are_requirements_met(
                    pkgdata,
                    pypackages.robot_env(),
                    pypackages.make_cache_extra_resolver(
                        self._get_cached_packages(installer)
                    ),
                )
                if not requirements_installed:
                    logger.warning("Project requirements not installed on RoboRIO")
                    for msg in desc:
                        logger.warning("- %s", msg)
                else:
                    logger.info("All project requirements already installed")

        #
        # Install requirements
        #

        if force_install:
            requirements_installed = False
        elif python_exists and not requirements_installed:
            # if this is a pre-existing robotpy install, warn the user
            # before changing their rio
            print(
                "\n"
                "Deployer has detected that the packages installed on your RoboRIO do not match\n"
                "the requirements in pyproject.toml. The installer will now:\n"
            )
            if not no_uninstall:
                prompt = "Continue with uninstall + install?"
                print("* Uninstall ALL Python packages from the RoboRIO")
            else:
                prompt = "Continue with install?"

            print(
                "* Install required packages on the RoboRIO\n"
                "\n"
                "If you do not wish to do this, specify --no-install as a deploy argument, or answer 'n'.\n"
            )

            if not yesno(prompt):
                requirements_installed = True

        if (
            cpp_java_exists
            or not python_exists
            or python_invalid
            or not requirements_installed
            or not kill_script_updated
        ):
            if no_install and not python_exists:
                raise Error(
                    "python3 was not found on the roboRIO\n"
                    "- could not install it because no-install was specified\n"
                    "- Use 'python -m robotpy installer install-python' to install python separately"
                )

            # This also will give more memory
            ssh.exec_bash(
                ". /etc/profile.d/frc-path.sh",
                ". /etc/profile.d/natinst-path.sh",
                robot_utils.kill_robot_cmd,
            )

            with installer.connect_to_robot(
                project_path=project_path,
                main_file=main_file,
                ignore_image_version=ignore_image_version,
                ssh=ssh,
            ):
                if not kill_script_updated:
                    robot_utils.update_kill_script(installer.ssh)

                if cpp_java_exists:
                    robot_utils.uninstall_cpp_java_admin(installer.ssh)

                if python_invalid:
                    with wrap_ssh_error("uninstalling python"):
                        self._clear_pip_packages(installer)
                        logger.info("Uninstalling %s from RoboRIO", python_invalid)
                        installer.ssh.exec_cmd(
                            f"opkg remove {python_invalid}",
                            check=True,
                            print_output=True,
                        )

                if not python_exists:
                    try:
                        installer.install_python()
                    except PythonMissingError as e:
                        raise PythonMissingError(
                            f"{e}\n\n"
                            "Run 'python -m robotpy sync' to download your project requirements from the internet (or --no-install to ignore)"
                        ) from e

                if not requirements_installed:
                    assert project is not None
                    packages = project.get_install_list()

                    # Check if everything is in the cache before doing the install
                    cached = self._get_cached_packages(installer)
                    ok, missing = project.are_requirements_met(
                        cached,
                        pypackages.robot_env(),
                        pypackages.make_cache_extra_resolver(cached),
                    )
                    if not ok:
                        errmsg = ["Project requirements not found in download cache!"]
                        errmsg.extend([f"- {msg}" for msg in missing])
                        errmsg += [
                            "",
                            "Run 'python -m robotpy sync' to download your project requirements",
                            "from the internet (or specify --no-install to not attempt installation).",
                        ]
                        raise Error("\n".join(errmsg))

                    if not no_uninstall:
                        logger.info(
                            "Clearing existing packages on RoboRIO before install (specify --no-uninstall to not do this)"
                        )
                        # The user may have deleted something from the project
                        # requirements so the only way to ensure the exact
                        # environment is to first clear the environment.
                        # - can't do a partial uninstall without completely
                        #   resolving everything
                        self._clear_pip_packages(installer)

                    logger.info("Installing project requirements on RoboRIO:")
                    for package in packages:
                        logger.info("- %s", package)

                    try:
                        installer.pip_install(False, False, False, False, [], packages)
                    except PipInstallError as e:
                        raise PipInstallError(
                            f"{e}\n\n"
                            "If 'no matching distribution found', run 'python -m robotpy sync' to download your\n"
                            "project requirements from the internet (or --no-install to ignore)."
                        ) from e

    def _do_deploy(
        self,
        ssh: sshcontroller.SshController,
        debug: bool,
        nc: bool,
        nc_ds: bool,
        robot_filename: str,
        project_path: pathlib.Path,
    ) -> bool:
        # This probably should be configurable... oh well

        # GradleRIO kills the robot before deploying it, so we do that too
        logger.info("Killing robot program")
        with wrap_ssh_error("killing robot program"):
            ssh.exec_bash(
                ". /etc/profile.d/frc-path.sh",
                ". /etc/profile.d/natinst-path.sh",
                "/usr/local/frc/bin/frcKillRobot.sh -t",
                check=False,
            )

        deploy_dir = pathlib.PurePosixPath("/home/lvuser")
        py_deploy_subdir = "py"
        py_new_deploy_subdir = "py_new"
        py_deploy_dir = deploy_dir / py_deploy_subdir

        # note below: deployed_cmd appears that it only can be a single line

        # In 2015, there were stdout/stderr issues. In 2016+, they seem to
        # have been fixed, but need to use -u for it to really work properly

        if debug:
            compileall_flags = ""
            deployed_cmd = (
                "env LD_LIBRARY_PATH=/usr/local/frc/lib/ "
                f"/usr/local/bin/python3 -u -m robotpy --main {py_deploy_dir}/{robot_filename} -v run"
            )
            deployed_cmd_fname = "robotDebugCommand"
            bash_cmd = "/bin/bash -cex"
        else:
            compileall_flags = "-O"
            deployed_cmd = (
                "env LD_LIBRARY_PATH=/usr/local/frc/lib/ "
                f"/usr/local/bin/python3 -u -O -m robotpy --main {py_deploy_dir}/{robot_filename} run"
            )
            deployed_cmd_fname = "robotCommand"
            bash_cmd = "/bin/bash -ce"

        py_new_deploy_dir = deploy_dir / py_new_deploy_subdir
        replace_cmd = f"rm -rf {py_deploy_dir}; mv {py_new_deploy_dir} {py_deploy_dir}"

        with wrap_ssh_error("configuring command"):
            ssh.exec_cmd(
                f'echo "{deployed_cmd}" > {deploy_dir}/{deployed_cmd_fname}', check=True
            )

        if debug:
            with wrap_ssh_error("touching frcDebug"):
                ssh.exec_cmd("touch /tmp/frcdebug", check=True)

        with wrap_ssh_error("removing stale deploy directory"):
            ssh.exec_cmd(f"rm -rf {py_new_deploy_dir}", check=True)

        logger.info("Copying new files to RoboRIO")

        # Copy the files over, copy to a temporary directory first
        # -> this is inefficient, but it's easier in sftp
        tmp_dir = pathlib.Path(tempfile.mkdtemp())
        try:
            py_tmp_dir = tmp_dir / py_new_deploy_subdir
            # Copy robot path contents to new deploy subdir
            self._copy_to_tmpdir(py_tmp_dir, project_path)

            # Copy 'build' artifacts to new deploy subdir
            with open(py_tmp_dir / "deploy.json", "w") as outf:
                json.dump(self._generate_build_data(project_path), outf)

            # sftp new deploy subdir to robot
            ssh.sftp(py_tmp_dir, deploy_dir, mkdir=True)
        finally:
            shutil.rmtree(tmp_dir)

        # start the netconsole listener now if requested, *before* we
        # actually start the robot code, so we can see all messages
        nc_thread = None
        if nc or nc_ds:
            nc_thread = self._start_nc(ssh, nc_ds)

        # Restart the robot code and we're done!
        sshcmd = (
            f"{bash_cmd} '"
            f"{replace_cmd};"
            f"/usr/local/bin/python3 {compileall_flags} -m compileall -q -r 5 /home/lvuser/py;"
            ". /etc/profile.d/frc-path.sh; "
            ". /etc/profile.d/natinst-path.sh; "
            f"chown -R lvuser:ni {py_deploy_dir}; "
            "sync; "
            "/usr/local/frc/bin/frcKillRobot.sh -t -r || true"
            "'"
        )

        logger.info("Starting robot code")
        logger.debug("SSH: %s", sshcmd)

        with wrap_ssh_error("starting robot code"):
            ssh.exec_cmd(sshcmd, check=True, print_output=True)

        if nc_thread is not None:
            nc_thread.join()

        return True

    def _start_nc(self, ssh: sshcontroller.SshController, nc_ds: bool):
        from netconsole import run  # type: ignore

        nc_event = threading.Event()
        nc_thread = threading.Thread(
            target=run,
            args=(ssh.hostname,),
            kwargs=dict(connect_event=nc_event, fakeds=nc_ds),
            daemon=True,
        )
        nc_thread.start()
        nc_event.wait(5)
        logger.info("Netconsole is listening...")
        return nc_thread

    def _copy_to_tmpdir(
        self, tmp_dir: pathlib.Path, project_path: pathlib.Path, dry_run: bool = False
    ):
        upload_files = []
        ignore_exts = frozenset({".pyc", ".whl", ".ipk", ".zip", ".gz", ".wpilog"})

        prefix_len = len(str(project_path)) + 1
        for root, dirs, files in os.walk(project_path):
            prefix = root[prefix_len:]
            if not dry_run:
                (tmp_dir / prefix).mkdir()

            # skip .svn, .git, .hg, etc directories
            for d in dirs[:]:
                if d.startswith(".") or d in ("__pycache__", "ctre_sim", "venv"):
                    dirs.remove(d)

            # skip .pyc files
            for filename in files:
                r, ext = splitext(filename)
                if ext in ignore_exts or r.startswith("."):
                    continue

                fname = join(root, filename)
                upload_files.append(fname)

                if not dry_run:
                    shutil.copy(fname, tmp_dir / prefix / filename)

        return upload_files
