import contextlib
import inspect
import io
import logging
import pathlib
import re
import shutil
import subprocess
import sys
from urllib.parse import urlparse
import typing

import click
from click import argument, option, group, pass_context, pass_obj, ClickException

from os.path import basename

from .version import version as __version__
from .cacheserver import CacheServer
from .errors import Error, SshExecError
from .sshcontroller import SshController, ssh_from_cfg
from .utils import _urlretrieve

_WPILIB_YEAR = "2024"
_IS_BETA = True

_ROBORIO_WHEELS = f"https://wpilib.jfrog.io/artifactory/api/pypi/wpilib-python-release-{_WPILIB_YEAR}/simple"

_ROBORIO_IMAGES = [
    "2024_v1.1",
]

_ROBORIO2_IMAGES = [
    "2024_v1.1",
]

_ROBOTPY_PYTHON_PLATFORM = "linux_roborio"
_ROBOTPY_PYTHON_VERSION_NUM = "312"
_ROBOTPY_PYTHON_VERSION = f"python{_ROBOTPY_PYTHON_VERSION_NUM}"

_PIP_STUB_PATH = "/home/admin/rpip"

_PYTHON_IPK = "https://github.com/robotpy/roborio-python/releases/download/2024-3.12.0-r1/python312_3.12.0-r1_cortexa9-vfpv3.ipk"

logger = logging.getLogger("robotpy.installer")


class RobotpyInstaller:
    def __init__(self, cache_root: pathlib.Path, cfgroot: pathlib.Path):
        self.cache_root = cache_root
        self.pip_cache = cache_root / "pip_cache"
        self.opkg_cache = cache_root / "opkg_cache"

        self.cfg_filename = cfgroot / ".installer_config"

    def log_startup(self) -> None:
        logger.info("RobotPy Installer %s", __version__)
        logger.info("-> caching files at %s", self.cache_root)

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
        result = ssh.check_output("opkg list-installed python38*").strip()

    if result != "":
        packages = [line.split()[0] for line in result.splitlines()]

        print("RobotPy 2020 components detected!")
        for package in packages:
            print("-", package)

        if not click.confirm("Uninstall?"):
            raise ClickException("installer cannot continue")

        with catch_ssh_error("uninstall old RobotPy"):
            ssh.exec_cmd(f"opkg remove {' '.join(packages)}", print_output=True)


def show_disk_space(
    ssh: SshController,
) -> typing.Tuple[str, str, str]:
    with catch_ssh_error("checking free space"):
        result = ssh.check_output("df -h / | tail -n 1")

    _, size, used, _, pct, _ = result.strip().split()
    logger.info("-> RoboRIO disk usage %s/%s (%s full)", used, size, pct)

    return size, used, pct


def roborio_checks(
    ssh: SshController,
    ignore_image_version: bool,
    pip_check: bool = False,
):
    #
    # Image version check
    #

    with catch_ssh_error("retrieving image version"):
        result = ssh.check_output(
            "grep IMAGEVERSION /etc/natinst/share/scs_imagemetadata.ini",
        )

    roborio_match = re.match(r'IMAGEVERSION = "(FRC_)?roboRIO_(.*)"', result.strip())
    roborio2_match = re.match(r'IMAGEVERSION = "(FRC_)?roboRIO2_(.*)"', result.strip())

    if roborio_match:
        version = roborio_match.group(2)
        images = _ROBORIO_IMAGES
        name = "RoboRIO"
    elif roborio2_match:
        version = roborio2_match.group(2)
        images = _ROBORIO2_IMAGES
        name = "RoboRIO 2"
    else:
        version = "<unknown>"
        images = [
            f"({_ROBORIO_IMAGES[-1]} | {_ROBORIO2_IMAGES[-1]})",
        ]
        name = "RoboRIO (1 | 2)"

    logger.info(f"-> {name} image version: {version}")

    if not ignore_image_version and version not in images:
        raise ClickException(
            f"{name} image {images[-1]} is required! Use --ignore-image-version to install anyways"
        )

    #
    # Free space check.. maybe in the future we'll use this to not accidentally
    # fill the user's disk, but it'd be annoying to figure out
    #

    show_disk_space(ssh)

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

        # Use pip stub to override the wheel platform on roborio
        with catch_ssh_error("copying pip stub"):
            from . import _pipstub

            stub_fp = io.BytesIO()
            stub_fp.write(b"#!/usr/local/bin/python3\n\n")
            stub_fp.write(inspect.getsource(_pipstub).encode("utf-8"))
            stub_fp.seek(0)

            ssh.sftp_fp(stub_fp, _PIP_STUB_PATH)
            ssh.exec_cmd(f"chmod +x {_PIP_STUB_PATH}", check=True)


#
# Click-based CLI
#


def _make_ssl_context(use_certifi: bool):
    if not use_certifi:
        return None

    try:
        import certifi
    except ImportError:
        raise click.ClickException(
            "certifi is not installed, please install it via `pip install certifi`"
        )

    import ssl

    return ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=certifi.where())


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


def opkg_install(
    installer: RobotpyInstaller,
    force_reinstall: bool,
    robot: str,
    ignore_image_version: bool,
    packages: typing.Sequence[pathlib.Path],
):
    """
    Installs opkg package on RoboRIO
    """

    installer.log_startup()

    for package in packages:
        if package.parent != installer.opkg_cache:
            raise ValueError("internal error")
        if not package.exists():
            raise ClickException(f"{package.name} has not been downloaded yet")

    # Write out the install script
    # -> we use a script because opkg doesn't have a good mechanism
    #    to only install a package if it's not already installed
    opkg_files = []

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
            pkgname, pkgversion, _ = package.name.split("_")

            opkg_script += "\n" + (
                opkg_script_bit
                % {
                    "fname": package.name,
                    "name": pkgname,
                    "version": pkgversion,
                }
            )

            opkg_files.append(package.name)

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

                sync
                ldconfig
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

        show_disk_space(ssh)


#
# python installation
#


def _get_python_ipk_path(installer: RobotpyInstaller) -> pathlib.Path:
    parts = urlparse(_PYTHON_IPK)
    return installer.opkg_cache / pathlib.PurePosixPath(parts.path).name


@installer.command()
@option("--use-certifi", is_flag=True, help="Use SSL certificates from certifi")
@pass_obj
def download_python(installer: RobotpyInstaller, use_certifi: bool):
    """
    Downloads Python to a folder to be installed
    """
    installer.opkg_cache.mkdir(parents=True, exist_ok=True)

    ipk_dst = _get_python_ipk_path(installer)
    _urlretrieve(_PYTHON_IPK, ipk_dst, True, _make_ssl_context(use_certifi))


@installer.command()
@_common_ssh_options
@pass_obj
def install_python(
    installer: RobotpyInstaller,
    robot: str,
    ignore_image_version: bool,
):
    """
    Installs Python on a RoboRIO.

    Requires download-python to be executed first.
    """
    ipk_dst = _get_python_ipk_path(installer)
    opkg_install(installer, False, robot, ignore_image_version, [ipk_dst])


@installer.command()
@_common_ssh_options
@pass_obj
def uninstall_python(
    installer: RobotpyInstaller,
    robot: str,
    ignore_image_version: bool,
):
    """Uninstall Python from a RoboRIO"""
    installer.log_startup()

    with installer.get_ssh(robot) as ssh:
        roborio_checks(ssh, ignore_image_version)

        with catch_ssh_error("removing packages"):
            ssh.exec_cmd(
                f"opkg remove {_ROBOTPY_PYTHON_VERSION}", check=True, print_output=True
            )

        show_disk_space(ssh)


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
        "--pre",
        is_flag=True,
        default=_IS_BETA,
        help="Include pre-release and development versions",
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
    cache: typing.Optional[CacheServer],
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
        if cache:
            fname = f"/requirements/{basename(req)}"
            cache.add_mapping(fname, req)
            pip_args.extend(["-r", f"http://localhost:{cache.port}{fname}"])
        else:
            pip_args.extend(["-r", req])


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
        import pip  # type: ignore
    except ImportError:
        raise ClickException("ERROR: pip must be installed to download python packages")

    installer.pip_cache.mkdir(parents=True, exist_ok=True)

    pip_args = [
        "--no-cache-dir",
        "--disable-pip-version-check",
        "download",
        "--extra-index-url",
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
        pip_args, None, force_reinstall, ignore_installed, no_deps, pre, requirements
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
    """

    installer.log_startup()

    if len(requirements) == 0 and len(packages) == 0:
        raise ClickException("You must give at least one requirement to install")

    with installer.get_ssh(robot) as ssh:
        roborio_checks(ssh, ignore_image_version, pip_check=True)

        cachesvr = installer.start_cache(ssh)

        pip_args = [
            "/home/admin/rpip",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "install",
            "--no-index",
            "--root-user-action=ignore",
            "--find-links",
            f"http://localhost:{cachesvr.port}/pip_cache/",
            # always add --upgrade, anything in the cache should be installed
            "--upgrade",
            "--upgrade-strategy=eager",
        ]

        _extend_pip_args(
            pip_args,
            cachesvr,
            force_reinstall,
            ignore_installed,
            no_deps,
            pre,
            requirements,
        )

        pip_args.extend(packages)

        with catch_ssh_error("installing packages"):
            ssh.exec_cmd(" ".join(pip_args), check=True, print_output=True)

        # Some of our hacky wheels require this
        with catch_ssh_error("running ldconfig"):
            ssh.exec_cmd("ldconfig")

        show_disk_space(ssh)


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
                f"{_PIP_STUB_PATH} --no-cache-dir --disable-pip-version-check list",
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
            _PIP_STUB_PATH,
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
