import argparse
import json
import logging
import pathlib
import typing


from .installer import RobotpyInstaller
from . import pyproject

logger = logging.getLogger("project")


class UpdateRobotpy:
    """
    Update the version of RobotPy your project depends on
    """

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--use-certifi",
            action="store_true",
            default=False,
            help="Use SSL certificates from certifi",
        )

    def run(self, project_path: pathlib.Path, use_certifi: bool) -> bool:
        try:
            project = pyproject.load(project_path)
        except FileNotFoundError:
            logger.error("Could not load pyproject.toml")
            return False

        print("Project robotpy version is", project.robotpy_version)

        installer = RobotpyInstaller(log_startup=False)

        # Determine what the latest version is
        v = installer.get_pypi_version("robotpy", use_certifi)
        print("Latest version of robotpy is", v)

        if project.robotpy_version > v:
            print("ERROR: refusing to update pyproject.toml!")
            return False

        # Update it in pyproject.toml
        pyproject.set_robotpy_version(project_path, v)

        return True


class Project:
    """
    Manage your robot project
    """

    subcommands = [
        ("update-robotpy", UpdateRobotpy),
    ]
