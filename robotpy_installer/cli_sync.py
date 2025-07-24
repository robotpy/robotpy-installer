import argparse
import inspect
import logging
import os
import pathlib
import subprocess
import sys
import tempfile

from packaging.version import Version

from .utils import handle_cli_error, yesno


from .installer import RobotpyInstaller
from . import pyproject

logger = logging.getLogger("sync")


class Sync:
    """
    Downloads SystemCore requirements and installs requirements locally

    The project requirements are determined by reading pyproject.toml. An
    example pyproject.toml is:

        [tool.robotpy]

        # Version of robotpy this project depends on
        robotpy_version = "{robotpy_version}"

        # Which extras should be installed
        # -> equivalent to `pip install robotpy[extra1, ...]
        robotpy_extras = []

        # Other pip packages to install (each element is equivalent to
        # a line in requirements.txt)
        requires = []

    If no pyproject.toml exists, a default is created using the current robotpy
    package version.

    You must be connected to the internet for this to work.
    """

    def __init__(self, parser: argparse.ArgumentParser):
        parser.add_argument(
            "--user",
            "-u",
            default=False,
            action="store_true",
            help="Use `pip install --user` to install packages",
        )

        parser.add_argument(
            "--use-certifi",
            action="store_true",
            default=False,
            help="Use SSL certificates from certifi",
        )

        parser.add_argument(
            "--no-install",
            action="store_true",
            default=False,
            help="Do not install any packages",
        )

        parser.add_argument(
            "--no-upgrade-project",
            action="store_true",
            default=False,
            help="Do not check to see if the project can be upgraded",
        )

    @handle_cli_error
    def run(
        self,
        project_path: pathlib.Path,
        main_file: pathlib.Path,
        no_install: bool,
        no_upgrade_project: bool,
        user: bool,
        use_certifi: bool,
    ):
        if not main_file.exists():
            print(
                f"ERROR: is this a robot project? {main_file} does not exist",
                file=sys.stderr,
            )
            return 1

        installer = RobotpyInstaller()

        # parse pyproject.toml to determine the requirements
        project = pyproject.load(project_path, write_if_missing=True)
        logger.info(
            "RobotPy version in `pyproject.toml` is '%s'", project.robotpy_version
        )

        # Check for upgrade
        if not no_upgrade_project:
            latest_robotpy_version = installer.get_pypi_version("robotpy", use_certifi)
            logger.info("Latest version of RobotPy is '%s'", latest_robotpy_version)
            if project.robotpy_version < latest_robotpy_version:
                msg = f"Update robotpy_version in `pyproject.toml` to {latest_robotpy_version}?"
                if yesno(msg):
                    pyproject.set_robotpy_version(project_path, latest_robotpy_version)
                    project.robotpy_version = latest_robotpy_version

        # Get the local version and don't accidentally downgrade them
        try:
            local_robotpy_version = Version(pyproject.robotpy_installed_version())
            if project.robotpy_version < local_robotpy_version:
                logger.warning(
                    "pyproject.toml robotpy version is older than currently installed version"
                )
                print()
                msg = (
                    f"Version currently installed: {local_robotpy_version}\n"
                    f"Version in `pyproject.toml`: {project.robotpy_version}\n"
                    "- Should we downgrade robotpy?"
                )
                if not yesno(msg):
                    print(
                        "Please update your pyproject.toml with the desired version of robotpy"
                    )
                    return False
        except pyproject.NoRobotpyError:
            pass

        packages = project.get_install_list()

        logger.info("Robot project requirements:")
        for package in packages:
            logger.info("- %s", package)

        #
        # First, download requirements for SystemCore
        #

        logger.info("Downloading Python for SystemCore")
        installer.download_python(use_certifi)

        logger.info("Downloading SystemCore python packages")
        installer.pip_download(
            no_deps=False,
            pre=False,
            requirements=[],
            packages=packages,
        )

        #
        # Local requirement installation
        # - On windows we can experience sharing violations if this package
        #   is upgraded, so we exit with the pip installation
        #
        if not no_install:
            logger.info("Installing requirements in local python interpreter")

            pip_args = [
                sys.executable,
                "-m",
                "pip",
                "--disable-pip-version-check",
                "install",
            ]
            if user:
                pip_args.append("--user")
            pip_args.extend(packages)

            # POSIX systems are easy, just execv and we're done
            if sys.platform != "win32":
                os.execv(sys.executable, pip_args)

            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py") as fp:
                fp.write(
                    inspect.cleandoc(
                        f"""
                        import os, subprocess
                        subprocess.run({pip_args!r})
                        print()
                        input("Install complete, press enter to continue")
                        os.unlink(__file__)
                    """
                    )
                )

            print("pip is launching in a new window to complete the installation")
            subprocess.Popen(
                [sys.executable, fp.name], creationflags=subprocess.CREATE_NEW_CONSOLE
            )
