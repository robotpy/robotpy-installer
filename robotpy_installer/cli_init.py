import argparse
import inspect
import logging
import pathlib
import urllib.request


from . import pyproject
from .utils import handle_cli_error


logger = logging.getLogger("init")


class Init:
    """
    Initializes a robot project
    """

    def __init__(self, parser: argparse.ArgumentParser):
        pass

    def _default_main_file(self, main_file: pathlib.Path)-> None:
        source = "https://raw.githubusercontent.com/robotpy/examples/refs/heads/main/ArcadeDrive/robot.py"
        with open(main_file, "wb") as fp:
            try:
                with urllib.request.urlopen(source) as response:
                    fp.write(response.read())
                logger.info("Created %s from example", main_file)
            except Exception as e:
                logger.error("Failed to download %s: %s", source, e)
                self._default_main_file_fallback(main_file)

    def _default_main_file_fallback(self, main_file: pathlib.Path)-> None:
        with open(main_file, "w") as fp:
            fp.write("# TODO: insert robot code here\n")
        logger.info("Created empty %s", main_file)

    @handle_cli_error
    def run(self, main_file: pathlib.Path, project_path: pathlib.Path):
        project_path.mkdir(parents=True, exist_ok=True)

        # Create robot.py if it doesn't already exist
        if not main_file.exists():
            self._default_main_file(main_file)

        # Create pyproject.toml if it doesn't already exist
        pyproject_path = pyproject.toml_path(project_path)
        if not pyproject_path.exists():
            pyproject.write_default_pyproject(project_path)

            logger.info("Created %s", pyproject_path)

        # Create .gitignore if it doesn't already exist
        gitignore_path = pyproject.gitignore_path(project_path)
        if not gitignore_path.exists():
            pyproject.write_default_gitignore(project_path)

            logger.info("Created %s", gitignore_path)
