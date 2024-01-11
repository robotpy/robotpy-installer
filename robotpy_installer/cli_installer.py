import argparse
import pathlib
import shutil
import typing

from .utils import handle_cli_error

from .installer import (
    InstallerException,
    RobotpyInstaller,
    _IS_BETA,
)
from .utils import yesno


def _add_ssh_options(parser: argparse.ArgumentParser):
    parser.add_argument(
        "--robot",
        help="Specify the robot hostname or team number",
    )

    parser.add_argument(
        "--ignore-image-version",
        action="store_true",
        default=False,
        help="Ignore RoboRIO image version",
    )


#
# installer cache
#


class InstallerCacheLocation:
    """Print cache location"""

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        pass

    @handle_cli_error
    def run(self):
        installer = RobotpyInstaller(log_startup=False)
        print(installer.cache_root)


class InstallerCacheRm:
    """Delete all cached files"""

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "-f",
            "--force",
            action="store_true",
            default=False,
            help="Force removal without asking",
        )

    @handle_cli_error
    def run(self, force: bool):
        installer = RobotpyInstaller(log_startup=False)
        if force or yesno(f"Really delete {installer.cache_root}?"):
            shutil.rmtree(installer.cache_root)


class InstallerCache:
    """
    Installer cache management
    """

    subcommands = [
        ("location", InstallerCacheLocation),
        ("rm", InstallerCacheRm),
    ]


#
# Installer python
#


class InstallerDownloadPython:
    """
    Downloads Python for RoboRIO

    You must be connected to the internet for this to work.
    """

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--use-certifi",
            action="store_true",
            default=False,
            help="Use SSL certificates from certifi",
        )

    def run(self, use_certifi: bool):
        installer = RobotpyInstaller()
        installer.download_python(use_certifi)


class InstallerInstallPython:
    """
    Installs Python on a RoboRIO
    """

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        _add_ssh_options(parser)

    @handle_cli_error
    def run(
        self,
        project_path: pathlib.Path,
        main_file: pathlib.Path,
        ignore_image_version: bool,
        robot: typing.Optional[str],
    ):
        installer = RobotpyInstaller()
        with installer.connect_to_robot(
            project_path=project_path,
            main_file=main_file,
            robot_or_team=robot,
            ignore_image_version=ignore_image_version,
        ):
            installer.install_python()


class InstallerUninstallPython:
    """
    Uninstall Python from a RoboRIO
    """

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        _add_ssh_options(parser)

    @handle_cli_error
    def run(
        self,
        project_path: pathlib.Path,
        main_file: pathlib.Path,
        ignore_image_version: bool,
        robot: typing.Optional[str],
    ):
        installer = RobotpyInstaller()
        with installer.connect_to_robot(
            project_path=project_path,
            main_file=main_file,
            robot_or_team=robot,
            ignore_image_version=ignore_image_version,
        ):
            installer.uninstall_python()


#
# Installer pip things
#


def common_pip_options(
    parser: argparse.ArgumentParser,
):
    parser.add_argument(
        "--no-deps",
        action="store_true",
        default=False,
        help="Don't install package dependencies",
    )

    parser.add_argument(
        "--pre",
        action="store_true",
        default=_IS_BETA,
        help="Include pre-release and development versions",
    )

    parser.add_argument(
        "--requirements",
        "-r",
        action="append",
        type=pathlib.Path,
        default=[],
        help="Install from the given requirements file. This option can be used multiple times.",
    )

    parser.add_argument(
        "packages",
        nargs="*",
        help="Packages to be processed",
    )


class InstallerDownload:
    """
    Specify Python package(s) to download, and store them in the cache.

    You must be connected to the internet for this to work.
    """

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        common_pip_options(parser)

    @handle_cli_error
    def run(
        self,
        no_deps: bool,
        pre: bool,
        requirements: typing.Tuple[str],
        packages: typing.Tuple[str],
    ):
        installer = RobotpyInstaller()
        installer.pip_download(no_deps, pre, requirements, packages)


class InstallerInstall:
    """
    Installs Python package(s) on a RoboRIO.

    The package must already been downloaded with the 'download' command first.
    """

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--force-reinstall",
            action="store_true",
            default=False,
            help="When upgrading, reinstall all packages even if they are already up-to-date.",
        )

        parser.add_argument(
            "--ignore-installed",
            "-I",
            action="store_true",
            default=False,
            help="Ignore the installed packages (reinstalling instead)",
        )

        common_pip_options(parser)
        _add_ssh_options(parser)

    @handle_cli_error
    def run(
        self,
        project_path: pathlib.Path,
        main_file: pathlib.Path,
        ignore_image_version: bool,
        robot: typing.Optional[str],
        force_reinstall: bool,
        ignore_installed: bool,
        no_deps: bool,
        pre: bool,
        requirements: typing.Tuple[str],
        packages: typing.Tuple[str],
    ):
        if len(requirements) == 0 and len(packages) == 0:
            raise InstallerException(
                "You must give at least one requirement to install"
            )

        installer = RobotpyInstaller()
        with installer.connect_to_robot(
            project_path=project_path,
            main_file=main_file,
            robot_or_team=robot,
            ignore_image_version=ignore_image_version,
        ):
            installer.pip_install(
                force_reinstall, ignore_installed, no_deps, pre, requirements, packages
            )


class InstallerList:
    """
    Lists Python packages present on RoboRIO
    """

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        _add_ssh_options(parser)

    @handle_cli_error
    def run(
        self,
        project_path: pathlib.Path,
        main_file: pathlib.Path,
        ignore_image_version: bool,
        robot: typing.Optional[str],
    ):
        installer = RobotpyInstaller()
        with installer.connect_to_robot(
            project_path=project_path,
            main_file=main_file,
            robot_or_team=robot,
            ignore_image_version=ignore_image_version,
            log_usage=False,
        ):
            installer.pip_list()


class InstallerUninstall:
    """
    Uninstall Python packages from a RoboRIO
    """

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        _add_ssh_options(parser)

        parser.add_argument(
            "packages",
            nargs="*",
            help="Packages to be processed",
        )

    @handle_cli_error
    def run(
        self,
        project_path: pathlib.Path,
        main_file: pathlib.Path,
        ignore_image_version: bool,
        robot: typing.Optional[str],
        packages: typing.List[str],
    ):
        installer = RobotpyInstaller()
        with installer.connect_to_robot(
            project_path=project_path,
            main_file=main_file,
            robot_or_team=robot,
            ignore_image_version=ignore_image_version,
        ):
            installer.pip_uninstall(packages)


#
# Installer command
#


class Installer:
    """
    Manage RobotPy on your RoboRIO
    """

    subcommands = [
        ("cache", InstallerCache),
        ("download", InstallerDownload),
        ("download-python", InstallerDownloadPython),
        ("install", InstallerInstall),
        ("install-python", InstallerInstallPython),
        ("list", InstallerList),
        ("uninstall", InstallerUninstall),
        ("uninstall-python", InstallerUninstallPython),
    ]
