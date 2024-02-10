import argparse
import pathlib
import shutil
import typing

from . import pypackages, roborio_utils
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


class _BasicInstallerCmd:
    log_usage = True

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
            log_usage=self.log_usage,
        ):
            self.on_run(installer)

    def on_run(self, installer: RobotpyInstaller):
        raise NotImplementedError()


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


class InstallerCacheList:
    """List python packages in cache"""

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        pass

    @handle_cli_error
    def run(self):
        installer = RobotpyInstaller(log_startup=False)
        packages = pypackages.get_pip_cache_packages(installer.cache_root)
        for pkg in sorted(packages.keys()):
            versions = map(str, sorted(packages[pkg]))
            print(f"{pkg}: {' '.join(versions)}")


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
        ("list", InstallerCacheList),
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


class InstallerInstallPython(_BasicInstallerCmd):
    """
    Installs Python on a RoboRIO
    """

    def on_run(self, installer: RobotpyInstaller):
        installer.install_python()


class InstallerUninstallPython(_BasicInstallerCmd):
    """
    Uninstall Python from a RoboRIO
    """

    def on_run(self, installer: RobotpyInstaller):
        installer.uninstall_python()


class InstallerUninstallRobotPy:
    """
    Uninstall RobotPy and user programs from a RoboRIO
    """

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        _add_ssh_options(parser)
        parser.add_argument("-y", "--yes", action="store_true", default=False)

    @handle_cli_error
    def run(
        self,
        project_path: pathlib.Path,
        main_file: pathlib.Path,
        ignore_image_version: bool,
        robot: typing.Optional[str],
        yes: bool,
    ):
        if not yes and not yesno(
            "This will delete all python and user data! Continue?"
        ):
            return

        installer = RobotpyInstaller()
        with installer.connect_to_robot(
            project_path=project_path,
            main_file=main_file,
            robot_or_team=robot,
            ignore_image_version=ignore_image_version,
        ):
            installer.uninstall_robotpy()


class InstallerUninstallJavaCpp(_BasicInstallerCmd):
    """
    Uninstall FRC Java/C++ programs from a RoboRIO
    """

    def on_run(self, installer: RobotpyInstaller):
        if not roborio_utils.uninstall_cpp_java_lvuser(installer.ssh):
            roborio_utils.uninstall_cpp_java_admin(installer.ssh)


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
        requirements: typing.Tuple[pathlib.Path],
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
        requirements: typing.Tuple[pathlib.Path],
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


class InstallerNiWebEnable(_BasicInstallerCmd):
    """
    Enables the NI web server and starts it
    """

    def on_run(self, installer: RobotpyInstaller):
        installer.ssh.exec_bash(
            "update-rc.d -f systemWebServer defaults",
            "/etc/init.d/systemWebServer start",
            check=True,
            print_output=True,
        )


class InstallerNiWebDisable(_BasicInstallerCmd):
    """
    Stops the NI web server and disables it from starting
    """

    def on_run(self, installer: RobotpyInstaller):
        installer.ssh.exec_bash(
            "/etc/init.d/systemWebServer stop",
            "update-rc.d -f systemWebServer remove",
            check=True,
            print_output=True,
        )


class InstallerNiWeb:
    """
    Manipulates the NI web server

    The NI web server on the RoboRIO takes up a lot of memory, and isn't
    used for very much. Use these commands to enable or disable it.
    """

    subcommands = [
        ("enable", InstallerNiWebEnable),
        ("disable", InstallerNiWebDisable),
    ]


class InstallerList(_BasicInstallerCmd):
    """
    Lists Python packages present on RoboRIO
    """

    log_usage = False

    def on_run(self, installer: RobotpyInstaller):
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
        ("niweb", InstallerNiWeb),
        ("uninstall", InstallerUninstall),
        ("uninstall-python", InstallerUninstallPython),
        ("uninstall-robotpy", InstallerUninstallRobotPy),
        ("uninstall-frc-java-cpp", InstallerUninstallJavaCpp),
    ]
