#!/usr/bin/env python3
#
# (C) 2014-2018 Dustin Spicuzza. Distributed under MIT license.
#
# This is a simple (ha!) installer program that is designed to be used to
# deploy RobotPy to a roborio via SSH
#
# It is intended to work on Windows, OSX, and Linux.
#

import argparse
import inspect
import logging
import os
import subprocess
import sys
import threading
from distutils.version import LooseVersion
from os.path import abspath, basename, dirname, exists, join

from robotpy_installer import __version__
from robotpy_installer.cacheserver import CacheServer
from robotpy_installer.errors import SshExecError, ArgError, Error, OpkgError
from robotpy_installer.opkgrepo import OpkgRepo
from robotpy_installer.sshcontroller import ssh_from_cfg

_FEEDS = [
    "https://www.tortall.net/~robotpy/feeds/2020",
    "https://download.ni.com/ni-linux-rt/feeds/2019/arm/cortexa9-vfpv3",
]

_ROBORIO_IMAGES = ["2020_v10"]

_ROBOTPY_PYTHON_VERSION = "python38"

logger = logging.getLogger("robotpy.installer")


class RobotpyInstaller(object):
    """
        Logic for installing RobotPy
    """

    # opkg feed
    opkg_arch = "cortexa9-vfpv3"

    commands = [
        "install-robotpy",
        "download-robotpy",
        "install",
        "download",
        "install-pip",
        "download-pip",
        "install-opkg",
        "download-opkg",
        "list-opkg",
        "search-opkg",
    ]

    def __init__(self, cache_root):

        self.cache_root = cache_root

        self.cfg_filename = abspath(join(cache_root, ".installer_config"))

        self.pip_cache = abspath(join(cache_root, "pip_cache"))
        self.opkg_cache = abspath(join(cache_root, "opkg_cache"))

        if not exists(self.pip_cache):
            os.makedirs(self.pip_cache)

        self._ctrl = None
        self._hostname = None
        self.chsrvr = None
        self.remote_commands = []

        # code, message
        self.error_checks = {}

    def _get_opkg(self):
        opkg = OpkgRepo(self.opkg_cache, self.opkg_arch)
        for feed in _FEEDS:
            opkg.add_feed(feed)
        return opkg

    def _add_image_check(self):

        cmd = "IV=$(grep IMAGEVERSION /etc/natinst/share/scs_imagemetadata.ini); echo $IV; "
        for image in _ROBORIO_IMAGES:
            cmd += '[ "$IV" == \'IMAGEVERSION = "FRC_roboRIO_%s"\' ] || ' % image
        cmd += (
            "(echo '-> ERROR: installer requires RoboRIO image %s! Use --ignore-image-version to force install' && /bin/false)"
            % _ROBORIO_IMAGES[-1]
        )

        self.remote_commands.append("(%s)" % cmd)

    def set_hostname(self, hostname):
        """Set the hostname or the team number"""
        if self._ctrl is not None:
            raise ValueError("internal error: too late")
        self._hostname = hostname

    @property
    def ctrl(self):
        if self._ctrl is None:
            self._ctrl = ssh_from_cfg(
                self.cfg_filename,
                username="admin",
                password="",
                hostname=self._hostname,
            )
        return self._ctrl

    def execute_remote(self):
        if len(self.remote_commands) > 0:
            try:
                if self.chsrvr is not None:
                    threading.Thread(
                        target=self.chsrvr.handle_requests, daemon=True
                    ).start()

                self.ctrl.ssh_exec_commands(
                    " && ".join(self.remote_commands),
                    existing_connection=self.chsrvr is not None,
                )

                if self.chsrvr is not None:
                    self.chsrvr.close()
                    self.chsrvr = None
            except SshExecError as e:
                for code, message in self.error_checks.items():
                    if e.retval == code:
                        logger.error(message)
                        break
                else:
                    raise

    #
    # Commands
    #

    #
    # RobotPy install commands
    #

    def _create_rpy_pip_options(self, options):
        # Construct an appropriate line to install
        options.requirement = []
        options.packages = ["pynetworktables"]

        options.force_reinstall = False
        options.ignore_installed = False
        options.no_deps = True

        if options.basever is not None:
            options.packages = [
                "%s==%s" % (pkg, options.basever) for pkg in options.packages
            ]

        if not options.no_tools:
            options.packages.append("robotpy-wpilib-utilities")

        return options

    def _create_rpy_opkg_options(self, options):
        # Construct an appropriate line to install
        options.requirement = []
        options.packages = [
            _ROBOTPY_PYTHON_VERSION,
            _ROBOTPY_PYTHON_VERSION + "-wpilib",
        ]
        options.upgrade = True

        options.force_reinstall = False
        options.ignore_installed = False

        return options

    def install_robotpy_opts(self, parser):
        parser.add_argument(
            "--basever", default=None, help="Install a specific version of WPILib et al"
        )
        parser.add_argument(
            "--no-tools",
            action="store_true",
            default=False,
            help="Don't install robotpy-wpilib-utilities",
        )
        parser.add_argument(
            "--pre",
            action="store_true",
            default=False,
            help="Include pre-release and development versions.",
        )
        parser.add_argument("--no-index", action="store_true", default=False)
        parser.add_argument(
            "--ignore-image-version", action="store_true", default=False
        )

    def install_robotpy(self, options):
        """
            This will copy the appropriate RobotPy components to the robot, and install
            them. If the components are already installed on the robot, then they will
            be reinstalled.
        """
        opkg_options = self._create_rpy_opkg_options(options)
        self.install_opkg(opkg_options)

        # We always add --pre to install-robotpy, in case the user downloaded
        # a prerelease version. Never add --pre without user intervention
        # for download-robotpy, however
        pip_options = self._create_rpy_pip_options(options)
        pip_options.pre = True
        # Also always upgrade
        pip_options.upgrade = True
        return self.install_pip(pip_options)

    # These share the same options
    download_robotpy_opts = install_robotpy_opts

    def download_robotpy(self, options):
        """
            This will update the cached RobotPy packages to the newest versions available.
        """

        self.download_opkg(self._create_rpy_opkg_options(options))

        return self.download_pip(self._create_rpy_pip_options(options))

    #
    # OPKG install commands
    #

    def download_opkg_opts(self, parser):
        parser.add_argument("packages", nargs="*", help="Packages to download")
        parser.add_argument(
            "--force-reinstall",
            action="store_true",
            default=False,
            help="When upgrading, reinstall all packages even if they are already up-to-date.",
        )
        parser.add_argument(
            "-r",
            "--requirement",
            action="append",
            default=[],
            help="Download from the given requirements file. This option can be used multiple times.",
        )
        parser.add_argument("--no-index", action="store_true", default=False)

    def install_opkg_opts(self, parser):
        self.download_opkg_opts(parser)
        parser.add_argument(
            "--ignore-image-version", action="store_true", default=False
        )

    def _load_opkg_from_req(self, *files):
        """
            Pull the list of opkgs from the files
        """
        opkgs = []
        # Loop through the passed in files to support multiple requirements files
        for file in files:
            with open(file, "r") as f:
                for row in f.readlines():
                    # Ignore commented lines and empty lines
                    stripped = row.strip()
                    if stripped and not stripped.startswith("#"):
                        # Add the package to the list of packages (and remove leading and trailing whitespace)
                        opkgs.append(stripped)
        return opkgs

    def download_opkg(self, options):
        """
            Specify opkg package(s) to download, and store them in the cache
        """

        opkg = self._get_opkg()
        if not options.no_index:
            opkg.update_packages()
        if options.requirement:
            packages = self._resolve_opkg_names(
                opkg, self._load_opkg_from_req(*options.requirement)
            )
        else:
            packages = self._resolve_opkg_names(opkg, options.packages)

        package_list = opkg.resolve_pkg_deps(packages)
        for package in package_list:
            opkg.download(package)

    def install_opkg(self, options):

        if not options.ignore_image_version:
            self._add_image_check()

        opkg = self._get_opkg()

        # Write out the install script
        # -> we use a script because opkg doesn't have a good mechanism
        #    to only install a package if it's not already installed
        opkg_files = []
        if options.requirement:
            package_list = self._resolve_opkg_names(
                opkg, self._load_opkg_from_req(*options.requirement)
            )
        else:
            package_list = self._resolve_opkg_names(opkg, options.packages)
        package_list = opkg.resolve_pkg_deps(package_list)

        opkg_script = inspect.cleandoc(
            """
            set -e
            PACKAGES=()
            DO_INSTALL=0
        """
        )

        if self.chsrvr is None:
            self.chsrvr = CacheServer(self.ctrl, self.cache_root)

        opkg_script_bit = inspect.cleandoc(
            f"""
            if ! opkg list-installed | grep -F "%(name)s - %(version)s"; then
                PACKAGES+=("http://localhost:{self.chsrvr.pipe_port}/opkg_cache/%(fname)s")
                DO_INSTALL=1
            else
                echo "%(name)s already installed"
            fi
        """
        )

        for package in package_list:
            try:
                pkg, fname = opkg.get_cached_pkg(package)
            except OpkgError as e:
                raise Error(e)

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
                opkg install %(options)s ${PACKAGES[@]}
            fi
        """
            )
            % {"options": "--force-reinstall" if options.force_reinstall else ""}
        )

        self.remote_commands.append(f"echo '{opkg_script}' > install_opkg.sh")
        self.remote_commands.append("bash install_opkg.sh")
        self.remote_commands.append("rm install_opkg.sh")

    def _resolve_opkg_names(self, opkg, packages):
        resolved = []
        for pkg in packages:
            try:
                opkg.get_pkginfo(pkg)
                resolved.append(pkg)
            except OpkgError as e:
                for prefix in (
                    _ROBOTPY_PYTHON_VERSION,
                    _ROBOTPY_PYTHON_VERSION + "-robotpy",
                ):
                    try:
                        opkg.get_pkginfo(prefix + "-" + pkg)
                        resolved.append(prefix + "-" + pkg)
                    except OpkgError:
                        pass
                    else:
                        break
                else:
                    raise e
        return resolved

    def _get_opkg_packages(self, options):
        opkg = self._get_opkg()
        if not options.no_index:
            opkg.update_packages()

        for feed in opkg.feeds:
            for pkgname, pkgdata in feed["pkgs"].items():
                for pkg in pkgdata:
                    yield pkg

    def list_opkg_opts(self, parser):
        parser.add_argument("--no-index", action="store_true", default=False)

    def list_opkg(self, options):
        data = set()
        for pkg in self._get_opkg_packages(options):
            data.add("%(Package)s - %(Version)s" % pkg)

        for v in sorted(data):
            print(v)

    def search_opkg_opts(self, parser):
        self.list_opkg_opts(parser)
        parser.add_argument("search")

    def search_opkg(self, options):
        # TODO: make this more intelligent...
        data = set()
        option = options.search
        for pkg in self._get_opkg_packages(options):
            if option in pkg["Package"] or option in pkg.get("Description", ""):
                data.add("%(Package)s - %(Version)s" % pkg)
        for v in sorted(data):
            print(v)

    #
    # Pip install commands
    #

    def download_pip_opts(self, parser):
        parser.add_argument(
            "packages",
            nargs="*",
            help="Packages to download/install, may be a local file",
        )
        parser.add_argument(
            "-r",
            "--requirement",
            action="append",
            default=[],
            help="Install from the given requirements file. This option can be used multiple times.",
        )
        parser.add_argument(
            "--pre",
            action="store_true",
            default=False,
            help="Include pre-release and development versions.",
        )

        # Various pip arguments
        parser.add_argument(
            "-U",
            "--upgrade",
            action="store_true",
            default=False,
            help="Upgrade packages (ignored when downloading, always downloads new packages)",
        )

        parser.add_argument(
            "--force-reinstall",
            action="store_true",
            default=False,
            help="When upgrading, reinstall all packages even if they are already up-to-date.",
        )
        parser.add_argument(
            "-I",
            "--ignore-installed",
            action="store_true",
            default=False,
            help="Ignore the installed packages (reinstalling instead).",
        )

        parser.add_argument(
            "--no-deps",
            action="store_true",
            default=False,
            help="Don't install package dependencies.",
        )

    def _process_pip_args(self, options, no_upgrade=False):
        pip_args = []
        if options.pre:
            pip_args.append("--pre")
        if options.upgrade and not no_upgrade:
            pip_args.append("--upgrade")
        if options.force_reinstall:
            pip_args.append("--force-reinstall")
        if options.ignore_installed:
            pip_args.append("--ignore-installed")
        if options.no_deps:
            pip_args.append("--no-deps")

        return pip_args

    def download_pip(self, options):
        """
            Specify python package(s) to download, and store them in the cache
        """

        try:
            import pip
        except ImportError:
            raise Error("ERROR: pip must be installed to download python packages")

        # Old pip args
        pip_args = [
            "--no-cache-dir",
            "--disable-pip-version-check",
            "install",
            "--no-binary",
            ":all:",
            "--download",
            self.pip_cache,
        ]

        try:
            pip_version = LooseVersion(pip.__version__)
        except:
            pass
        else:
            if pip_version >= LooseVersion("8.0"):
                pip_args = [
                    "--no-cache-dir",
                    "--disable-pip-version-check",
                    "download",
                    "--no-binary",
                    ":all:",
                    "-d",
                    self.pip_cache,
                ]

        if len(options.requirement) == 0 and len(options.packages) == 0:
            raise ArgError("You must give at least one requirement to install")

        pip_args.extend(self._process_pip_args(options, no_upgrade=True))

        for r in options.requirement:
            pip_args.extend(["-r", r])

        pip_args.extend(options.packages)
        pip_args = [sys.executable, "-m", "pip"] + pip_args

        return subprocess.call(pip_args)

    # These share the same options
    install_pip_opts = download_pip_opts

    def install_pip(self, options):
        """
            Copies python packages over to the roboRIO, and installs them. If the
            package already has been installed, it will not be upgraded. Use -U to
            upgrade a package.
        """

        if len(options.requirement) == 0 and len(options.packages) == 0:
            raise ArgError("You must give at least one requirement to install")

        # TODO
        # Deal with requirements.txt files specially, because we have to
        # copy the file over.

        # copy them to the cache with a unique name, and delete them later?
        if len(options.requirement) != 0:
            raise NotImplementedError()

        self.remote_commands.append("([ -x /usr/local/bin/pip3 ] || exit 87)")
        self.error_checks[87] = (
            "pip3 not found, did you install RobotPy?\n\n"
            + "Use the download-robotpy and install-robotpy commands to install."
        )

        if self.chsrvr is None:
            self.chsrvr = CacheServer(self.ctrl, self.cache_root)

        links_ref = f"http://localhost:{self.chsrvr.pipe_port}/pip_cache/"

        cmd = f"/usr/local/bin/pip3 install --no-index --find-links={links_ref} "

        cmd_args = options.packages

        cmd += " ".join(self._process_pip_args(options) + cmd_args)
        self.remote_commands.append(cmd)

    # Backwards-compatibility aliases
    install_opts = install_pip_opts
    install = install_pip
    download_opts = download_pip_opts
    download = download_pip


def main(args=None):

    if args is None:
        args = sys.argv[1:]

    log_datefmt = "%H:%M:%S"
    log_format = "%(asctime)s:%(msecs)03d %(levelname)-8s: %(name)-20s: %(message)s"

    logging.basicConfig(datefmt=log_datefmt, format=log_format, level=logging.INFO)

    # Because this is included with the RobotPy download package, there
    # are two ways to use this:
    #
    # * If there are directories 'pip_cache' and 'opkg_cache' next to this file,
    #   then use that
    # * Otherwise, use the current working directory
    #

    cache_root = abspath(join(dirname(__file__)))
    if not exists(join(cache_root, "pip_cache")):
        cache_root = os.getcwd()

    try:
        installer = RobotpyInstaller(cache_root)
    except Error as e:
        print("ERROR: %s" % e)
        return 1

    # argparse boilerplate...
    parser = argparse.ArgumentParser()
    subparser = parser.add_subparsers(dest="command", help="Commands")
    subparser.required = True

    # shared options
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--robot", default=None, help="Specify the robot hostname (overrides --team)"
    )
    shared.add_argument(
        "--team", default=None, type=int, help="Specify team number to deploy for"
    )

    # Setup various options
    for command in installer.commands:
        fn = getattr(installer, command.replace("-", "_"))
        opt_fn = getattr(installer, command.replace("-", "_") + "_opts")
        cmdparser = subparser.add_parser(
            command, help=inspect.getdoc(fn), parents=[shared]
        )
        opt_fn(cmdparser)
        cmdparser.set_defaults(cmdobj=fn)

    options = parser.parse_args(args)
    if options.robot:
        installer.set_hostname(options.robot)
    elif options.team:
        installer.set_hostname(options.team)

    logger.info("RobotPy Installer %s", __version__)
    logger.info("-> caching files at %s", cache_root)

    try:
        retval = options.cmdobj(options)
        installer.execute_remote()
    except ArgError as e:
        parser.error(str(e))
        retval = 1
    except Error as e:
        logger.error(str(e))
        retval = 1

    if retval is None:
        retval = 0
    elif retval is True:
        retval = 0
    elif retval is False:
        retval = 1

    return retval


if __name__ == "__main__":
    retval = main()
    exit(retval)
