#!/usr/bin/env python3

from __future__ import annotations

description = """
Standalone bootstrap script that can install from a bundle created with make-offline.
It will ignore your local installation and install whatever is in the bundle
"""


import argparse
import dataclasses
import json
import os.path
import pathlib
import shutil
import subprocess
import sys
import tempfile
import typing
import zipfile

METADATA_VERSION = 1
METADATA_JSON = "rpybundle.json"


@dataclasses.dataclass
class Metadata:
    """
    Describes content of METADATA_JSON in offline bundle
    """

    #: metadata version
    version: int

    #: robotpy-installer version
    installer_version: str

    #: wpilib year
    wpilib_year: str

    #: python ipk name
    python_ipk: str

    #: python wheel tags supported by this bundle
    wheel_tags: typing.List[str]

    # #: list of packages derived from pyproject.toml
    # packages: typing.List[str]

    def dumps(self) -> str:
        data = dataclasses.asdict(self)
        return json.dumps(data)

    @classmethod
    def loads(cls, s: str) -> Metadata:
        data = json.loads(s)
        if not isinstance(data, dict):
            raise ValueError("invalid metadata")
        version = data.get("version", None)
        if not isinstance(version, int):
            raise ValueError(f"invalid metadata version {version!r}")
        if version > METADATA_VERSION:
            raise ValueError(
                f"can only understand metadata < {METADATA_VERSION}, got {version}"
            )

        installer_version = data.get("installer_version", None)
        if not isinstance(installer_version, str):
            raise ValueError(f"invalid installer version {installer_version!r}")

        wpilib_year = data.get("wpilib_year", None)
        if not isinstance(wpilib_year, str):
            raise ValueError(f"invalid wpilib_year {wpilib_year}")

        python_ipk = data.get("python_ipk", None)
        if not python_ipk or not isinstance(python_ipk, str):
            raise ValueError(f"invalid python_ipk value")

        wheel_tags = data.get("wheel_tags", None)
        if not isinstance(wheel_tags, list) or len(wheel_tags) == 0:
            raise ValueError(f"no wheel tags present")
        # packages = data.get("packages")
        # if not isinstance(packages, list):
        #     raise ValueError(f"invalid package list {packages!r}")

        return Metadata(
            version=version,
            installer_version=installer_version,
            wpilib_year=wpilib_year,
            python_ipk=python_ipk,
            wheel_tags=wheel_tags,  # packages=packages
        )


if __name__ == "__main__":

    # If is running from a zipfile, identify it
    bundle_path = None
    if pathlib.Path(__file__).parent.is_file():
        bundle_path = pathlib.Path(__file__).parent

    parser = argparse.ArgumentParser(
        description=description, formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
            "--user",
            "-u",
            default=False,
            action="store_true",
            help="Use `pip install --user` to install packages",
        )

    if bundle_path is None:
        parser.add_argument("bundle", type=pathlib.Path, help="Bundle file")

    # parser.add_argument(
    #     "--no-robotpy-installer",
    #     default=False,
    #     action="store_true",
    #     help="Do not install robotpy-installer",
    # )
    args = parser.parse_args()

    if bundle_path is None:
        bundle_path= args.bundle

    with zipfile.ZipFile(bundle_path, "r") as zfp:

        # extract metadata
        raw_metadata = zfp.read(METADATA_JSON).decode("utf-8")
        metadata = Metadata.loads(raw_metadata)

        cache_root = pathlib.Path.home() / "wpilib" / metadata.wpilib_year / "robotpy"

        # extract pip cache to a temporary directory
        with tempfile.TemporaryDirectory() as t:
            pip_cache = pathlib.Path(t)


            print("Extracting wheels to temporary path...")

            for info in zfp.infolist():
                p = pathlib.Path(info.filename.replace("/", os.path.sep))
                if p.parts[0] == "pip_cache":
                    with zfp.open(info) as sfp, open(pip_cache / p.name, "wb") as dfp:
                        shutil.copyfileobj(sfp, dfp)

            # TODO: when doing local dev, how do I embed the right
            #       robotpy-installer? or just ignore it

            # if not args.no_robotpy_installer:
            #     print("Installing robotpy-installer", metadata.installer_version)

            #     # install robotpy-installer offline from temporary directory
            #     # do == to force pip to install this one
            #     pip_args = [
            #         sys.executable,
            #         "-m",
            #         "pip",
            #         "install",
            #         "--disable-pip-version-check",
            #         "--no-index",
            #         "--find-links",
            #         t,
            #         f"robotpy-installer=={metadata.installer_version}",
            #     ]
            #     print("+", *pip_args)
            #     subprocess.check_call(pip_args)

            # If this is part of robotpy-installer, on Windows need
            # to worry about sharing violation

    sync_args = [
        sys.executable,
        "-m",
        "robotpy",
        "sync",
        "--from",
        str(bundle_path)
    ]

    if args.user:
        sync_args.append("--user")

    result = subprocess.run(sync_args)
    sys.exit(result.returncode)

