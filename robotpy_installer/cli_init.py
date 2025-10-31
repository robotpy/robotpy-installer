import argparse
import logging
import pathlib
import typing as T

from packaging.version import Version

from . import installer
from . import pyproject
from .errors import Error
from .utils import handle_cli_error


logger = logging.getLogger("init")


class Init:
    """
    Initializes a robot project
    """

    def __init__(self, parser: argparse.ArgumentParser):
        parser.add_argument(
            "version",
            nargs="?",
            help="Manually specify the RobotPy package version to use instead of autodetecting it",
            type=Version,
        )

    @handle_cli_error
    def run(
        self,
        main_file: pathlib.Path,
        project_path: pathlib.Path,
        version: T.Optional[Version],
    ):

        supported_year = int(installer._WPILIB_YEAR)
        if version is not None and version.major != int(installer._WPILIB_YEAR):
            msg = (
                f"Only RobotPy {supported_year}.x is supported by this version "
                f"of robotpy-installer (specified {version})"
            )
            raise Error(msg)

        project_path.mkdir(parents=True, exist_ok=True)

        # Create robot.py if it doesn't already exist
        # - TODO: it would be neat if it could download an example from github
        if not main_file.exists():
            with open(main_file, "w") as fp:
                fp.write("# TODO: insert robot code here\n")

            logger.info("Created empty %s", main_file)

        # Create pyproject.toml if it doesn't already exist
        pyproject_path = pyproject.toml_path(project_path)
        if not pyproject_path.exists():
            pyproject.write_default_pyproject(
                project_path, str(version) if version is not None else None
            )

            logger.info("Created %s", pyproject_path)

        # Create .gitignore if it doesn't already exist
        gitignore_path = pyproject.gitignore_path(project_path)
        if not gitignore_path.exists():
            pyproject.write_default_gitignore(project_path)

            logger.info("Created %s", gitignore_path)
