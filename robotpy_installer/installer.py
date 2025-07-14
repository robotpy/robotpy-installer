import contextlib
import inspect
import io
import json
import logging
import pathlib
import re
import shlex
import subprocess
import sys
from urllib.parse import urlparse
import typing

from os.path import basename, exists

from packaging.version import Version

from .version import version as __version__
from . import robot_utils
from .cacheserver import CacheServer
from .errors import Error, SshExecError
from .sshcontroller import SshController, ssh_from_cfg
from .utils import _urlretrieve

_WPILIB_YEAR = "2025"
_IS_BETA = False

_ROBORIO_WHEELS = f"https://wpilib.jfrog.io/artifactory/api/pypi/wpilib-python-release-{_WPILIB_YEAR}/simple"

_ROBORIO_IMAGES = [
    "2025_v2.0",
]

_ROBORIO2_IMAGES = [
    "2025_v2.0",
]

_ROBOTPY_PYTHON_PLATFORM = "linux_roborio"
_ROBOTPY_PYTHON_VERSION_TUPLE = (3, 13)
_ROBOTPY_PYTHON_VERSION_NUM = "".join(map(str, _ROBOTPY_PYTHON_VERSION_TUPLE))
_ROBOTPY_PYTHON_VERSION = f"python{_ROBOTPY_PYTHON_VERSION_NUM}"

_PIP_STUB_PATH = "/home/admin/rpip"

_PYTHON_IPK = "https://github.com/robotpy/roborio-python/releases/download/2025-3.13.1-r1/python313_3.13.1-r1_cortexa9-vfpv3.ipk"

logger = logging.getLogger("robotpy.installer")


class InstallerException(Error):
    pass


class PipInstallError(InstallerException):
    pass


class PythonMissingError(InstallerException):
    pass


@contextlib.contextmanager
def catch_ssh_error(msg: str):
    try:
        yield
    except SshExecError as e:
        raise InstallerException(f"{msg}: {e}")


class RobotpyInstaller:
    def __init__(self, *, log_startup: bool = True):
        self.cache_root = pathlib.Path.home() / "wpilib" / _WPILIB_YEAR / "robotpy"
        self.pip_cache = self.cache_root / "pip_cache"
        self.opkg_cache = self.cache_root / "opkg_cache"

        self._ssh: typing.Optional[SshController] = None
        self._cache_server: typing.Optional[CacheServer] = None

        self._image_version_ok = False
        self._robot_pip_ok = False

        if log_startup:
            logger.info("RobotPy Installer %s", __version__)
            logger.info("-> caching files at %s", self.cache_root)

    @contextlib.contextmanager
    def connect_to_robot(
        self,
        *,
        project_path: pathlib.Path,
        main_file: pathlib.Path,
        robot_or_team: typing.Union[None, str, int] = None,
        ignore_image_version: bool = False,
        log_usage: bool = True,
        no_resolve: bool = False,
        ssh: typing.Optional[SshController] = None,
    ):
        if ssh is None:
            ssh = ssh_from_cfg(
                project_path,
                main_file,
                username="admin",
                password="",
                robot_or_team=robot_or_team,
                no_resolve=no_resolve,
            )
        elif ssh.username != "admin":
            ssh = SshController(ssh.hostname, "admin", "")

        with ssh:
            self._ssh = ssh

            self.ensure_image_version(ignore_image_version)

            if log_usage:
                self.show_disk_space()
                self.show_mem_usage()

            yield

            if log_usage:
                self.show_disk_space()
                self.show_mem_usage()

            self._ssh = None

    @property
    def cache_server(self) -> CacheServer:
        """Only access inside connect_to_robot context"""
        if not self._cache_server:
            self._cache_server = CacheServer(self.ssh, self.cache_root)
            self._cache_server.start()

        return self._cache_server

    @property
    def ssh(self) -> SshController:
        """Only access inside connect_to_robot context"""
        if self._ssh is None:
            raise RuntimeError("internal error")
        return self._ssh

    #
    # Utilities
    #

    def opkg_install(
        self,
        force_reinstall: bool,
        packages: typing.Sequence[pathlib.Path],
    ):
        """
        Installs opkg package on RoboRIO
        """

        for package in packages:
            if package.parent != self.opkg_cache:
                raise ValueError("internal error")
            if not package.exists():
                raise PythonMissingError(
                    f"{package.name} has not been downloaded yet\n"
                    "- Use 'python -m robotpy installer download-python' to download"
                )

        # Write out the install script
        # -> we use a script because opkg doesn't have a good mechanism
        #    to only install a package if it's not already installed
        opkg_files = []

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
                PACKAGES+=("http://localhost:{self.cache_server.port}/opkg_cache/%(fname)s")
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
            self.ssh.exec_cmd(
                f"echo '{opkg_script}' > /tmp/install_opkg.sh",
                check=True,
            )

        with catch_ssh_error("installing selected packages"):
            self.ssh.exec_cmd(
                "bash /tmp/install_opkg.sh", check=True, print_output=True
            )

        try:
            self.ssh.exec_cmd("rm /tmp/install_opkg.sh")
        except SshExecError:
            pass

    def show_disk_space(
        self,
    ) -> typing.Tuple[str, str, str]:
        #
        # Free space check.. maybe in the future we'll use this to not accidentally
        # fill the user's disk, but it'd be annoying to figure out
        #

        with catch_ssh_error("checking free space"):
            result = self.ssh.check_output("df -h / | tail -n 1")

        _, size, used, _, pct, _ = result.strip().split()
        logger.info("-> RoboRIO disk usage %s/%s (%s full)", used, size, pct)

        return size, used, pct

    def show_mem_usage(self):
        with catch_ssh_error("checking memory info"):
            result = self.ssh.check_output("cat /proc/meminfo")

        total_kb = 0
        available_kb = 0
        found = 0

        for line in result.strip().splitlines():
            if line.startswith("MemTotal:"):
                total_kb = int(line.split()[1])
                found += 1
            elif line.startswith("MemAvailable"):
                available_kb = int(line.split()[1])
                found += 1

            if found == 2:
                break

        used_kb = total_kb - available_kb
        pct_free = (available_kb / float(total_kb)) * 100.0

        logger.info(
            "-> RoboRIO memory %.1fM/%.1fM (%.0f%% full)",
            used_kb / 1000.0,
            total_kb / 1000.0,
            pct_free,
        )

    def ensure_image_version(self, ignore_image_version: bool):
        if self._image_version_ok:
            return

        with catch_ssh_error("retrieving image version"):
            result = self.ssh.check_output(
                "grep IMAGEVERSION /etc/natinst/share/scs_imagemetadata.ini",
            )

        roborio_match = re.match(
            r'IMAGEVERSION = "(FRC_)?roboRIO_(.*)"', result.strip()
        )
        roborio2_match = re.match(
            r'IMAGEVERSION = "(FRC_)?roboRIO2_(.*)"', result.strip()
        )

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
            raise InstallerException(
                f"{name} image {images[-1]} is required!\n"
                "\n"
                "See https://docs.wpilib.org/en/stable/docs/zero-to-robot/step-3/imaging-your-roborio.html\n"
                "for information about upgrading the RoboRIO image.\n"
                "\n"
                "Use --ignore-image-version to install anyways"
            )

        self._image_version_ok = True

    def ensure_robot_pip(self):
        if self._robot_pip_ok:
            return

        #
        # Ensure that pip is installed
        #

        with catch_ssh_error("checking for pip3"):
            if self.ssh.exec_cmd("[ -x /usr/local/bin/pip3 ]").returncode != 0:
                raise InstallerException(
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

            self.ssh.sftp_fp(stub_fp, _PIP_STUB_PATH)
            self.ssh.exec_cmd(f"chmod +x {_PIP_STUB_PATH}", check=True)

        self._robot_pip_ok = True

    #
    # Python installation
    #

    @property
    def _python_ipk_path(self) -> pathlib.Path:
        parts = urlparse(_PYTHON_IPK)
        return self.opkg_cache / pathlib.PurePosixPath(parts.path).name

    def is_python_downloaded(self) -> bool:
        return self._python_ipk_path.exists()

    def download_python(self, use_certifi: bool):
        self.opkg_cache.mkdir(parents=True, exist_ok=True)

        ipk_dst = self._python_ipk_path
        _urlretrieve(_PYTHON_IPK, ipk_dst, True, _make_ssl_context(use_certifi))

    def install_python(self):
        """
        Installs Python on a RoboRIO.

        Requires download-python to be executed first.
        """
        logger.info("Installing Python on RoboRIO (this may take a few minutes)")
        ipk_dst = self._python_ipk_path
        self.opkg_install(False, [ipk_dst])

    def uninstall_python(
        self,
    ):
        with catch_ssh_error("removing python"):
            self.ssh.exec_cmd(
                f"opkg remove {_ROBOTPY_PYTHON_VERSION}",
                check=True,
                print_output=True,
            )

    def uninstall_robotpy(self):
        with catch_ssh_error("removing user program"):
            self.ssh.exec_bash(
                robot_utils.kill_robot_cmd,
                "rm -rf /home/lvuser/py",
                f"rm -f {robot_utils.robot_command}",
            )

        with catch_ssh_error("removing pip packages"):
            pkgs = robot_utils.get_rio_py_packages(self.ssh)
            self.pip_uninstall([pkg for pkg in pkgs.keys() if pkg != "pip"])

        self.uninstall_python()

    #
    # pip packages
    #

    def _extend_pip_args(
        self,
        pip_args: typing.List[str],
        cache: typing.Optional[CacheServer],
        force_reinstall: bool,
        ignore_installed: bool,
        no_deps: bool,
        pre: bool,
        requirements: typing.Iterable[pathlib.Path],
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
                fname = f"/requirements/{req.name}"
                cache.add_mapping(fname, str(req))
                pip_args.extend(["-r", f"http://localhost:{cache.port}{fname}"])
            else:
                pip_args.extend(["-r", str(req)])

    def pip_download(
        self,
        no_deps: bool,
        pre: bool,
        requirements: typing.Sequence[pathlib.Path],
        packages: typing.Sequence[str],
    ):
        """
        Specify Python package(s) to download, and store them in the cache.

        You must be connected to the internet for this to work.
        """

        if not requirements and not packages:
            raise InstallerException(
                "You must give at least one requirement to download"
            )

        try:
            import pip  # type: ignore
        except ImportError:
            raise InstallerException(
                "ERROR: pip must be installed to download python packages"
            )

        self.pip_cache.mkdir(parents=True, exist_ok=True)

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
            str(self.pip_cache),
        ]

        self._extend_pip_args(
            pip_args,
            None,
            False,
            False,
            no_deps,
            pre,
            requirements,
        )

        pip_args.extend(packages)
        pip_args = [sys.executable, "-m", "robotpy_installer._pipstub"] + pip_args

        logger.debug("Using pip to download: %s", pip_args)

        retval = subprocess.call(pip_args)
        if retval != 0:
            raise InstallerException("pip download failed")

    def pip_install(
        self,
        force_reinstall: bool,
        ignore_installed: bool,
        no_deps: bool,
        pre: bool,
        requirements: typing.Sequence[pathlib.Path],
        packages: typing.Sequence[str],
    ):
        """
        Installs Python package(s) on a RoboRIO.

        The package must already been downloaded with the 'download' command first.
        """

        self.ensure_robot_pip()

        if len(requirements) == 0 and len(packages) == 0:
            raise InstallerException(
                "You must give at least one requirement to install"
            )

        cache_server = self.cache_server

        pip_args = [
            "/home/admin/rpip",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "install",
            "--no-index",
            "--root-user-action=ignore",
            "--find-links",
            f"http://localhost:{cache_server.port}/pip_cache/",
            # always add --upgrade, anything in the cache should be installed
            "--upgrade",
            "--upgrade-strategy=eager",
        ]

        self._extend_pip_args(
            pip_args,
            cache_server,
            force_reinstall,
            ignore_installed,
            no_deps,
            pre,
            requirements,
        )

        for package in packages:
            if package.endswith(".whl") and exists(package):
                fname = basename(package)
                cache_server.add_mapping(f"/extra/{fname}", package)
                pip_args.append(f"http://localhost:{cache_server.port}/extra/{fname}")
            else:
                pip_args.append(package)

        try:
            self.ssh.exec_cmd(shlex.join(pip_args), check=True, print_output=True)
        except SshExecError as e:
            raise PipInstallError(f"installing packages: {e}") from e

        # Some of our hacky wheels require this
        with catch_ssh_error("running ldconfig"):
            self.ssh.exec_cmd("ldconfig")

    def pip_list(self):
        self.ensure_robot_pip()

        with catch_ssh_error("pip3 list"):
            self.ssh.exec_cmd(
                f"{_PIP_STUB_PATH} --no-cache-dir --disable-pip-version-check list",
                check=True,
                print_output=True,
            )

    def pip_uninstall(
        self,
        packages: typing.Sequence[str],
    ):
        self.ensure_robot_pip()

        if len(packages) == 0:
            raise InstallerException("You must give at least one package to uninstall")

        pip_args = [
            _PIP_STUB_PATH,
            "--no-cache-dir",
            "--disable-pip-version-check",
            "uninstall",
            "--root-user-action=ignore",
            "--yes",
        ]
        pip_args.extend(packages)

        with catch_ssh_error("uninstalling packages"):
            self.ssh.exec_cmd(shlex.join(pip_args), check=True, print_output=True)

    def get_pypi_version(self, package: str, use_certifi: bool) -> Version:
        """
        Retrieves the latest version of a package on pypi that corresponds to the current year
        """
        self.cache_root.mkdir(parents=True, exist_ok=True)
        fname = self.cache_root / f"pypi-{package}.json"
        _urlretrieve(
            f"https://pypi.org/simple/{package}",
            fname,
            True,
            _make_ssl_context(use_certifi),
            False,
            {"Accept": "application/vnd.pypi.simple.v1+json"},
        )
        with open(fname, "r") as fp:
            data = json.load(fp)

        versions = [Version(v) for v in data["versions"]]

        # Sort the versions
        maxv = Version(str(int(_WPILIB_YEAR) + 1))

        def _version_ok(v: Version) -> bool:
            ok = v < maxv and not v.is_devrelease
            if ok and not _IS_BETA:
                ok = not v.is_prerelease
            return ok

        versions = sorted(v for v in versions if _version_ok(v))
        if not versions:
            raise InstallerException(f"could not find {package} version on pypi")

        return versions[-1]


def _make_ssl_context(use_certifi: bool):
    if not use_certifi:
        return None

    try:
        import certifi  # type: ignore
    except ImportError:
        raise InstallerException(
            "certifi is not installed, please install it via `pip install certifi`"
        )

    import ssl

    return ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=certifi.where())


def main():
    print("ERROR: robotpy-installer is now a subcommand of 'robotpy'", file=sys.stderr)
    print("- Use 'python -m robotpy installer'", file=sys.stderr)
    sys.exit(1)
