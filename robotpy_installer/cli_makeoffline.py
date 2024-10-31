import argparse
import json
import logging
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

import packaging.tags

from . import pyproject
from .errors import Error
from .installer import RobotpyInstaller, _WPILIB_YEAR
from .utils import handle_cli_error

from . import _bootstrap

logger = logging.getLogger("bundler")


class MakeOffline:
    """
    Creates a bundle that can be used to install RobotPy dependencies
    without internet access.

    To install from the bundle,
    """

    def __init__(self, parser: argparse.ArgumentParser):
        parser.add_argument(
            "--use-certifi",
            action="store_true",
            default=False,
            help="Use SSL certificates from certifi",
        )
        parser.add_argument(
            "bundle_path",
            type=pathlib.Path
        )

    @handle_cli_error
    def run(
        self, project_path: pathlib.Path, use_certifi: bool, bundle_path: pathlib.Path
    ):

        installer = RobotpyInstaller()
        # local_cache = installer.cache_root
        local_pip_cache = installer.pip_cache

        bootstrap_py_path = pathlib.Path(_bootstrap.__file__)

        # collect deps from project, or use installed version
        project = pyproject.load(
            project_path, write_if_missing=False, default_if_missing=True
        )
        packages = project.get_install_list()

        logger.info("Robot project requirements:")
        for package in packages:
            logger.info("- %s", package)

        # Download python ipk to original cache
        python_ipk = installer.download_python(use_certifi)

        # Make temporary directory to download to
        with tempfile.TemporaryDirectory() as t:
            tpath = pathlib.Path(t)
            installer.set_cache_root(tpath)
            whl_path = tpath / "pip_cache"

            if True:
                whl_path.mkdir(parents=True, exist_ok=True)
            if False:
                # Download rio deps (use local cache to speed it up)
                installer.pip_download(False, False, [], packages, local_pip_cache)

                # Download local deps
                pip_args = [
                    sys.executable,
                    "-m",
                    "pip",
                    "--disable-pip-version-check",
                    "download",
                    "-d",
                    str(whl_path),
                ] + packages

                logger.debug("Using pip to download: %s", pip_args)
                retval = subprocess.call(pip_args)
                if retval != 0:
                    raise Error("pip download failed")

            from .version import version
            # TODO: it's possible for the bundle to include a version of robotpy-installer
            # that does not match the current version. Need to not do that.

            metadata = _bootstrap.Metadata(
                version=_bootstrap.METADATA_VERSION,
                installer_version=version,
                wpilib_year=_WPILIB_YEAR,
                python_ipk=python_ipk.name,
                wheel_tags=[
                    str(next(packaging.tags.sys_tags())),  # sys tag
                    # roborio tag
                ],
            )

            logger.info("Bundle supported wheel tags:")
            for tag in metadata.wheel_tags:
                logger.info("+ %s", tag)

            logger.info("Making bundle at '%s'", bundle_path)

            # zip it all up
            with zipfile.ZipFile(bundle_path, "w") as zfp:

                # descriptor
                logger.info("+ %s", _bootstrap.METADATA_JSON)
                zfp.writestr(_bootstrap.METADATA_JSON, metadata.dumps())

                # bootstrapper
                logger.info("+ __main__.py")
                zfp.write(bootstrap_py_path, "__main__.py")

                # ipk
                logger.info("+ %s", python_ipk.name)
                zfp.write(python_ipk, python_ipk.name)

                # pip cache
                for f in whl_path.iterdir():
                    logger.info("+ pip_cache/%s", f.name)
                    zfp.write(f, f"pip_cache/{f.name}")

            st = bundle_path.stat()
            logger.info("Bundle is %d bytes", st.st_size)
