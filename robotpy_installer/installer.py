import contextlib
import inspect
import logging
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import typing

import click
from click import argument, option, group, pass_context, pass_obj, ClickException

from distutils.version import LooseVersion
from os.path import basename

from .version import version as __version__
from .cacheserver import CacheServer
from .errors import Error, SshExecError, OpkgError
from .opkgrepo import OpkgRepo
from .sshcontroller import SshController, ssh_from_cfg

_WPILIB_YEAR = "2021"

_OPKG_ARCH = "cortexa9-vfpv3"


_OPKG_FEEDS = [
    f"https://www.tortall.net/~robotpy/feeds/{_WPILIB_YEAR}",
    f"https://download.ni.com/ni-linux-rt/feeds/2019/arm/{_OPKG_ARCH}",
]

_ROBORIO_WHEELS = f"https://www.tortall.net/~robotpy/wheels/{_WPILIB_YEAR}/roborio"

_ROBORIO_IMAGES = [
    "2020_v10",
    "2021_v1",
    "2021_v2",
    "2021_v3.0",
    "2021_v3.1",
]

_ROBOTPY_PYTHON_PLATFORM = "linux_armv7l"
_ROBOTPY_PYTHON_VERSION_NUM = "39"
_ROBOTPY_PYTHON_VERSION = f"python{_ROBOTPY_PYTHON_VERSION_NUM}"


logger = logging.getLogger("robotpy.installer")


class RobotpyInstaller:
    def __init__(self, cache_root: pathlib.Path, cfgroot: pathlib.Path):

        self.cache_root = cache_root
        self.pip_cache = cache_root / "pip_cache"
        self.opkg_cache = cache_root / "opkg_cache"

        self.cfg_filename = cfgroot / ".installer_config"

    def log_startup(self):
        logger.info("RobotPy Installer %s", __version__)
        logger.info("-> caching files at %s", self.cache_root)

    def get_opkg(self):
        opkg = OpkgRepo(self.opkg_cache, _OPKG_ARCH)
        for feed in _OPKG_FEEDS:
            opkg.add_feed(feed)
        return opkg

    def get_opkg_packages(self, no_index: bool):
        opkg = self.get_opkg()
        if not no_index:
            opkg.update_packages()

        for feed in opkg.feeds:
            for _, pkgdata in feed["pkgs"].items():
                for pkg in pkgdata:
                    yield pkg

    def get_ssh(self, robot: typing.Optional[str]) -> SshController:
        try:
            return ssh_from_cfg(
                self.cfg_filename, username="admin", password="", hostname=robot
            )
        except Error as e:
            raise ClickException(str(e)) from e

    def start_cache(self, ssh: SshController) -> CacheServer:
        cache = CacheServer(ssh, self.cache_root)
        cache.start()
        return cache


#
# Helpers
#


@contextlib.contextmanager
def catch_ssh_error(msg: str):
    try:
        yield
    except SshExecError as e:
        raise ClickException(f"{msg}: {e}")


def remove_legacy_components(ssh: SshController):

    # (remove in 2022) check for old robotpy components
    # -> only removes opkg components, pip will take care of the rest

    with catch_ssh_error("check for old RobotPy"):
        result = ssh.exec_cmd(
            "opkg list-installed python38*", get_output=True
        ).stdout.strip()

    if result != "":
        packages = [line.split()[0] for line in result.splitlines()]

        print("RobotPy 2020 components detected!")
        for package in packages:
            print("-", package)

        if not click.confirm("Uninstall?"):
            raise ClickException("installer cannot continue")

        with catch_ssh_error("uninstall old RobotPy"):
            result = ssh.exec_cmd(
                f"opkg remove {' '.join(packages)}", print_output=True
            )


def roborio_checks(
    ssh: SshController,
    ignore_image_version: bool,
    pip_check: bool = False,
):

    #
    # Image version check
    #

    with catch_ssh_error("retrieving image version"):
        result = ssh.exec_cmd(
            "grep IMAGEVERSION /etc/natinst/share/scs_imagemetadata.ini",
            get_output=True,
        )

    m = re.match(r'IMAGEVERSION = "FRC_roboRIO_(.*)"', result.stdout.strip())
    version = m.group(1) if m else "<unknown>"

    logger.info("-> RoboRIO image version %s", version)

    if not ignore_image_version and version not in _ROBORIO_IMAGES:
        raise ClickException(
            f"RoboRIO image {_ROBORIO_IMAGES[-1]} is required! Use --ignore-image-version to install anyways"
        )

    #
    # Free space check.. maybe in the future we'll use this to not accidentally
    # fill the user's disk, but it'd be annoying to figure out
    #

    with catch_ssh_error("checking free space"):
        result = ssh.exec_cmd("df -h / | tail -n 1", get_output=True)

    _, size, used, _, pct, _ = result.stdout.strip().split()
    logger.info("-> RoboRIO disk usage %s/%s (%s full)", used, size, pct)

    # Remove in 2022
    remove_legacy_components(ssh)

    #
    # Ensure that pip is installed
    #

    if pip_check:
        with catch_ssh_error("checking for pip3"):
            if ssh.exec_cmd("[ -x /usr/local/bin/pip3 ]").returncode != 0:
                raise ClickException(
                    inspect.cleandoc(
                        """
                        pip3 not found on RoboRIO, did you install python?

                        Use the 'download-python' and 'install-python' commands first!
                        """
                    )
                )


#
# Click-based CLI
#


@group()
@pass_context
def installer(ctx: click.Context):
    """
    RobotPy installer utility
    """

    log_datefmt = "%H:%M:%S"
    log_format = "%(asctime)s:%(msecs)03d %(levelname)-8s: %(name)-20s: %(message)s"

    logging.basicConfig(datefmt=log_datefmt, format=log_format, level=logging.INFO)

    cache_root = pathlib.Path.home() / "wpilib" / _WPILIB_YEAR / "robotpy"
    cfg_root = pathlib.Path.cwd()

    # This becomes the first argument to any cli command with the @pass_obj decorator
    ctx.obj = RobotpyInstaller(cache_root, cfg_root)


def _common_ssh_options(f):
    f = option("--robot", help="Specify the robot hostname or team number")(f)
    f = option(
        "--ignore-image-version", is_flag=True, help="Ignore RoboRIO image version"
    )(f)
    return f


#
# Cache management
#


@installer.group()
def cache():
    """Cache management"""


@cache.command()
@pass_obj
def location(installer: RobotpyInstaller):
    """Print cache location"""
    print(installer.cache_root)


@cache.command()
@option("-f", "--force", is_flag=True, help="Force removal without asking")
@pass_obj
def rm(installer: RobotpyInstaller, force: bool):
    """Delete all cached files"""
    if force or click.confirm(f"Really delete {installer.cache_root}?"):
        shutil.rmtree(installer.cache_root)


#
# OPkg related commands
#


@installer.group()
def opkg():
    """
    Advanced RoboRIO package management tools
    """


@opkg.command(name="download")
@option("--no-index", is_flag=True, help="Only examine local cache")
@option(
    "-r",
    "--requirements",
    type=click.Path(exists=True),
    multiple=True,
    default=[],
    help="Install from the given requirements file. This option can be used multiple times.",
)
@argument("packages", nargs=-1)
@pass_obj
def opkg_download(
    installer: RobotpyInstaller,
    no_index: bool,
    requirements: typing.Tuple[str],
    packages: typing.Tuple[str],
):
    """
    Downloads opkg package to local cache
    """

    installer.log_startup()

    try:
        opkg = installer.get_opkg()
        if not no_index:
            opkg.update_packages()

        if requirements:
            packages = list(packages) + opkg.load_opkg_from_req(*requirements)

        if not packages:
            raise ClickException("must specify packages to download")

        package_list = opkg.resolve_pkg_deps(packages)
        for package in package_list:
            opkg.download(package)
    except OpkgError as e:
        raise ClickException(str(e)) from e


@opkg.command(name="install")
@option(
    "--force-reinstall",
    is_flag=True,
    help="When upgrading, reinstall all packages even if they are already up-to-date.",
)
@option("--ignore-image-version", is_flag=True)
@option(
    "-r",
    "--requirements",
    type=click.Path(exists=True),
    multiple=True,
    default=[],
    help="Install from the given requirements file. This option can be used multiple times.",
)
@argument("packages", nargs=-1, required=True)
@_common_ssh_options
@pass_obj
def opkg_install(
    installer: RobotpyInstaller,
    force_reinstall: bool,
    requirements: typing.Tuple[str],
    robot: str,
    ignore_image_version: bool,
    packages: typing.Tuple[str],
):
    """
    Installs opkg package on RoboRIO
    """

    installer.log_startup()

    opkg = installer.get_opkg()

    # Write out the install script
    # -> we use a script because opkg doesn't have a good mechanism
    #    to only install a package if it's not already installed
    opkg_files = []
    if requirements:
        packages = [packages] + opkg.load_opkg_from_req(*requirements)

    try:
        packages = opkg.resolve_pkg_deps(packages)
    except OpkgError as e:
        raise ClickException(str(e))

    with installer.get_ssh(robot) as ssh:

        cache = installer.start_cache(ssh)

        roborio_checks(ssh, ignore_image_version)

        opkg_script = inspect.cleandoc(
            """
            set -e
            PACKAGES=()
            DO_INSTALL=0
            """
        )

        opkg_script_bit = inspect.cleandoc(
            f"""
            if ! opkg list-installed | grep -F "%(name)s - %(version)s"; then
                PACKAGES+=("http://localhost:{cache.port}/opkg_cache/%(fname)s")
                DO_INSTALL=1
            else
                echo "%(name)s already installed"
            fi
            """
        )

        for package in packages:
            try:
                pkg, fname = opkg.get_cached_pkg(package)
            except OpkgError as e:
                raise ClickException(str(e))

            opkg_script += "\n" + (
                opkg_script_bit
                % {
                    "fname": basename(fname),
                    "name": pkg["Package"],
                    "version": pkg["Version"],
                }
            )

            opkg_files.append(fname)

        # Finish it out
        opkg_script += "\n" + (
            inspect.cleandoc(
                """
                if [ "${DO_INSTALL}" == "0" ]; then
                    echo "No packages to install."
                else
                    echo + opkg install %(options)s ${PACKAGES[@]}
                    opkg install %(options)s ${PACKAGES[@]}
                fi
                """
            )
            % {"options": "--force-reinstall" if force_reinstall else ""}
        )

        with catch_ssh_error("creating opkg install script"):
            # write to /tmp so that it doesn't persist
            ssh.exec_cmd(
                f"echo '{opkg_script}' > /tmp/install_opkg.sh",
                check=True,
            )

        with catch_ssh_error("installing selected packages"):
            ssh.exec_cmd("bash /tmp/install_opkg.sh", check=True, print_output=True)

        try:
            ssh.exec_cmd("rm /tmp/install_opkg.sh")
        except SshExecError:
            pass


@opkg.command(name="list")
@option("--no-index", is_flag=True, help="Only examine local cache")
@pass_obj
def opkg_list(installer: RobotpyInstaller, no_index: bool):
    """
    List all packages in opkg database
    """

    data = set()
    for pkg in installer.get_opkg_packages(no_index):
        data.add("%(Package)s - %(Version)s" % pkg)

    for v in sorted(data):
        print(v)


@opkg.command(name="search")
@option("--no-index", is_flag=True, help="Only examine local cache")
@argument("search")
@pass_obj
def opkg_search(installer: RobotpyInstaller, no_index: bool, search: str):
    """
    Search opkg database for packages
    """

    # TODO: make this more intelligent...
    data = set()
    for pkg in installer.get_opkg_packages(no_index):
        if search in pkg["Package"] or search in pkg.get("Description", ""):
            data.add("%(Package)s - %(Version)s" % pkg)
    for v in sorted(data):
        print(v)


@opkg.command(name="uninstall")
@argument("packages", nargs=-1, required=True)
@_common_ssh_options
@pass_obj
def opkg_uninstall(
    installer: RobotpyInstaller,
    robot: str,
    ignore_image_version: bool,
    packages: typing.Tuple[str],
):
    installer.log_startup()

    with installer.get_ssh(robot) as ssh:
        roborio_checks(ssh, ignore_image_version)

        packages = " ".join(packages)

        with catch_ssh_error("removing packages"):
            ssh.exec_cmd(f"opkg remove {packages}", check=True, print_output=True)


#
# python installation
#


@installer.command()
@pass_context
def download_python(ctx: click.Context):
    """
    Downloads Python to a folder to be installed
    """
    ctx.forward(opkg_download, packages=[_ROBOTPY_PYTHON_VERSION])


@installer.command()
@_common_ssh_options
@pass_context
def install_python(
    ctx: click.Context,
    robot: str,
    ignore_image_version: bool,
):
    """
    Installs Python on a RoboRIO.

    Requires download-python to be executed first.
    """
    ctx.forward(opkg_install, packages=[_ROBOTPY_PYTHON_VERSION])


#
# Python package management
#


def _pip_options(f):

    f = option(
        "--force-reinstall",
        is_flag=True,
        help="When upgrading, reinstall all packages even if they are already up-to-date.",
    )(f)
    f = option(
        "--ignore-installed",
        "-I",
        is_flag=True,
        help="Ignore the installed packages (reinstalling instead)",
    )(f)
    f = option("--no-deps", is_flag=True, help="Don't install package dependencies")(f)
    f = option(
        "--pre", is_flag=True, help="Include pre-release and development versions"
    )(f)
    f = option(
        "--requirements",
        "-r",
        multiple=True,
        type=click.Path(exists=True),
        default=[],
        help="Install from the given requirements file. This option can be used multiple times.",
    )(f)

    f = argument("packages", nargs=-1)(f)
    return f


def _extend_pip_args(
    pip_args: typing.List[str],
    cache: CacheServer,
    force_reinstall: bool,
    ignore_installed: bool,
    no_deps: bool,
    pre: bool,
    requirements: typing.Sequence[str],
):
    if pre:
        pip_args.append("--pre")
    if force_reinstall:
        pip_args.append("--force-reinstall")
    if ignore_installed:
        pip_args.append("--ignore-installed")
    if no_deps:
        pip_args.append("--no-deps")

    for req in requirements:
        fname = f"/requirements/{basename(req)}"
        cache.add_mapping(fname, req)
        pip_args.extend(["-r", f"http://localhost:{cache.port}{fname}"])


@installer.command()
@_pip_options
@pass_obj
def download(
    installer: RobotpyInstaller,
    force_reinstall: bool,
    ignore_installed: bool,
    no_deps: bool,
    pre: bool,
    requirements: typing.Tuple[str],
    packages: typing.Tuple[str],
):
    """
    Specify Python package(s) to download, and store them in the cache.

    You must be connected to the internet for this to work.
    """

    installer.log_startup()

    if not requirements and not packages:
        raise ClickException("You must give at least one requirement to download")

    try:
        import pip
    except ImportError:
        raise ClickException("ERROR: pip must be installed to download python packages")

    try:
        pip_version = LooseVersion(pip.__version__)
    except:
        pass
    else:
        # TODO: what do we actually support? newer is better obviously...
        if pip_version < LooseVersion("18.0"):
            raise ClickException("robotpy-installer requires pip 18.0 or later")

    installer.pip_cache.mkdir(parents=True, exist_ok=True)

    pip_args = [
        "--no-cache-dir",
        "--disable-pip-version-check",
        "download",
        "--find-links",
        _ROBORIO_WHEELS,
        "--only-binary",
        ":all:",
        "--platform",
        _ROBOTPY_PYTHON_PLATFORM,
        "--python-version",
        _ROBOTPY_PYTHON_VERSION_NUM,
        "--implementation",
        "cp",
        "--abi",
        f"cp{_ROBOTPY_PYTHON_VERSION_NUM}",
        "-d",
        str(installer.pip_cache),
    ]

    _extend_pip_args(
        pip_args, cache, force_reinstall, ignore_installed, no_deps, pre, requirements
    )

    pip_args.extend(packages)
    pip_args = [sys.executable, "-m", "robotpy_installer._pipstub"] + pip_args

    logger.debug("Using pip to download: %s", pip_args)

    retval = subprocess.call(pip_args)
    if retval != 0:
        raise ClickException("pip download failed")


@installer.command(name="install")
@_pip_options
@_common_ssh_options
@pass_obj
def pip_install(
    installer: RobotpyInstaller,
    force_reinstall: bool,
    ignore_installed: bool,
    no_deps: bool,
    pre: bool,
    requirements: typing.Tuple[str],
    packages: typing.Tuple[str],
    ignore_image_version: bool,
    robot: str,
):
    """
    Installs Python package(s) on a RoboRIO.

    The package must already been downloaded with the 'download' command first.

    If the package already has been installed, it will not be upgraded.
    Use -U to upgrade a package.
    """

    installer.log_startup()

    if len(requirements) == 0 and len(packages) == 0:
        raise ClickException("You must give at least one requirement to install")

    with installer.get_ssh(robot) as ssh:

        roborio_checks(ssh, ignore_image_version, pip_check=True)

        cache = installer.start_cache(ssh)

        pip_args = [
            "/usr/local/bin/pip3",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "install",
            "--no-index",
            "--find-links",
            f"http://localhost:{cache.port}/pip_cache/",
            # always add --upgrade, anything in the cache should be installed
            "--upgrade",
            "--upgrade-strategy=eager",
        ]

        _extend_pip_args(
            pip_args,
            cache,
            force_reinstall,
            ignore_installed,
            no_deps,
            pre,
            requirements,
        )

        pip_args.extend(packages)

        with catch_ssh_error("installing packages"):
            ssh.exec_cmd(" ".join(pip_args), check=True, print_output=True)


@installer.command(name="list")
@_common_ssh_options
@pass_obj
def pip_list(
    installer: RobotpyInstaller,
    ignore_image_version: bool,
    robot: str,
):
    """
    Lists Python packages present on RoboRIO
    """
    installer.log_startup()

    with installer.get_ssh(robot) as ssh:

        roborio_checks(ssh, ignore_image_version, pip_check=True)

        with catch_ssh_error("pip3 list"):
            ssh.exec_cmd(
                "/usr/local/bin/pip3 --no-cache-dir --disable-pip-version-check list",
                check=True,
                print_output=True,
            )


@installer.command(name="uninstall")
@_common_ssh_options
@option(
    "--requirements",
    "-r",
    multiple=True,
    type=click.Path(exists=True),
    default=[],
    help="Install from the given requirements file. This option can be used multiple times.",
)
@argument("packages", nargs=-1)
@pass_obj
def pip_uninstall(
    installer: RobotpyInstaller,
    requirements: typing.Tuple[str],
    packages: typing.Tuple[str],
    ignore_image_version: bool,
    robot: str,
):
    """
    Uninstall Python packages from a RoboRIO
    """
    installer.log_startup()

    if len(requirements) == 0 and len(packages) == 0:
        raise ClickException("You must give at least one requirement to install")

    with installer.get_ssh(robot) as ssh:

        roborio_checks(ssh, ignore_image_version, pip_check=True)

        pip_args = [
            "/usr/local/bin/pip3",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "uninstall",
            "--yes",
        ]
        pip_args.extend(packages)

        with catch_ssh_error("uninstalling packages"):
            ssh.exec_cmd(" ".join(pip_args), check=True, print_output=True)


#
# Removed commands
#


@installer.command(hidden=True)
def download_robotpy():
    raise ClickException(
        inspect.cleandoc(
            """

        The download-robotpy command has been removed! The equivalent commands are now:

            robotpy-installer download-python
            robotpy-installer download robotpy

        Run "robotpy-installer --help" for details.
            """
        )
    )


@installer.command(hidden=True)
def install_robotpy():
    raise ClickException(
        inspect.cleandoc(
            """

        The install-robotpy command has been removed! The equivalent commands are now:

            robotpy-installer install-python
            robotpy-installer install robotpy

        Run "robotpy-installer --help" for details.
            """
        )
    )


# alias for backwards compat
main = installer

if __name__ == "__main__":
    installer()
