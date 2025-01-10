import argparse
import inspect
import logging
import pathlib

from . import pyproject
from .utils import handle_cli_error


logger = logging.getLogger("init")


class Init:
    """
    Initializes a robot project
    """

    def __init__(self, parser: argparse.ArgumentParser):
        pass

    @handle_cli_error
    def run(self, main_file: pathlib.Path, project_path: pathlib.Path):
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
            pyproject.write_default_pyproject(project_path)

            logger.info("Created %s", pyproject_path)

        # Create .gitignore if it doesn't already exist
        pyproject_path = pyproject.gitignore_path(project_path)
        if not pyproject_path.exists():
            pyproject.write_default_gitignore(project_path)

            logger.info("Created %s", pyproject_path)
