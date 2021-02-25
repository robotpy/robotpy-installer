import configparser
import io
import logging
import re
import os
from os.path import exists, join, expanduser, split as splitpath
from pathlib import PurePath, PurePosixPath
import typing

import paramiko

from robotpy_installer.errors import SshExecError, Error
from robotpy_installer.robotfinder import RobotFinder
from robotpy_installer.utils import _resolve_addr

logger = logging.getLogger("robotpy.installer")


class SuppressKeyPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        return


class SshExecResult(typing.NamedTuple):
    returncode: int
    stdout: typing.Optional[str]


class SshController(object):
    """
    Use this to execute commands on a roboRIO in a cross platform manner

    ::

        with SshController(hostname, username, password) as controller:
            controller.exec_cmd("cat /etc/lsb-release", print_output=True)

    """

    def __init__(self, hostname, username, password):
        self.username = username
        self.password = password
        self.hostname = hostname

        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(SuppressKeyPolicy)

    def __enter__(self):
        self.client.connect(
            self.hostname,
            username=self.username,
            password=self.password,
            allow_agent=False,
            look_for_keys=False,
        )
        return self

    def __exit__(self, *args):
        self.client.close()

    def exec_cmd(
        self,
        cmd: str,
        *,
        check: bool = False,
        get_output: bool = False,
        print_output: bool = False,
    ) -> SshExecResult:

        output = None
        buffer = io.StringIO()

        with self.client.get_transport().open_session() as channel:
            channel.set_combine_stderr(True)
            channel.exec_command(cmd)

            with channel.makefile("r") as stdout:
                for line in stdout:
                    if get_output:
                        buffer.write(line)
                    if print_output:
                        print(line, end="")

            retval = channel.recv_exit_status()

        if check and retval != 0:
            raise SshExecError(
                "Command '%s' returned non-zero error status %s" % (cmd, retval),
                retval,
            )
        elif get_output:
            output = buffer.getvalue()

        return SshExecResult(retval, output)

    def check_output(self, cmd: str, *, print_output: bool = False) -> str:
        result = self.exec_cmd(
            cmd,
            check=True,
            get_output=True,
            print_output=print_output,
        )
        assert result.stdout is not None
        return result.stdout

    def sftp(self, local_path, remote_path, mkdir=True):
        # from https://gist.github.com/johnfink8/2190472
        oldcwd = os.getcwd()
        sftp = self.client.open_sftp()
        try:
            remote_path = PurePosixPath(remote_path)
            parent, child = splitpath(local_path)
            os.chdir(parent)
            for d, _, files in os.walk(child):
                d = PurePath(d)
                try:
                    remote_dir = remote_path / d
                    print("make", remote_dir)
                    if not mkdir:
                        # skip first mkdir
                        mkdir = True
                    else:
                        sftp.mkdir(str(remote_dir))
                except:
                    raise
                for fname in files:
                    local_fname = d / fname
                    remote_fname = remote_dir / fname
                    print(local_fname.relative_to(child), "->", remote_fname)
                    sftp.put(str(local_fname), str(remote_fname))
        finally:
            os.chdir(oldcwd)
            sftp.close()


def ssh_from_cfg(
    cfg_filename: typing.Union[str, os.PathLike],
    username: str,
    password: str,
    hostname: typing.Optional[str] = None,
    no_resolve=False,
):
    # hostname can be a team number or an ip / hostname

    dirty = True
    cfg = configparser.ConfigParser()
    cfg.setdefault("auth", {})

    if exists(cfg_filename):
        cfg.read(cfg_filename)
        dirty = False

    if hostname is not None:
        dirty = True
        cfg["auth"]["hostname"] = str(hostname)

    hostname = cfg["auth"].get("hostname")

    if not hostname:
        dirty = True

        print("Robot setup (hit enter for default value):")
        while not hostname:
            hostname = input("Team number or robot hostname: ")

        cfg["auth"]["hostname"] = hostname

    if dirty:
        with open(cfg_filename, "w") as fp:
            cfg.write(fp)

    # see if an ssh alias exists
    try:
        with open(join(expanduser("~"), ".ssh", "config")) as fp:
            hn = hostname.lower()
            for line in fp:
                if re.match(r"\s*host\s+%s\s*" % hn, line.lower()):
                    no_resolve = True
                    break
    except Exception:
        pass

    # check to see if this is a team number
    team = None
    try:
        team = int(hostname.strip())
    except ValueError:
        # check to see if it matches a team hostname
        # -> allows legacy hostname configurations to benefit from
        #    the robot finder
        if not no_resolve:
            hostmod = hostname.lower().strip()
            m = re.search(r"10.(\d+).(\d+).2", hostmod)
            if m:
                team = int(m.group(1)) * 100 + int(m.group(2))
            else:
                m = re.match(r"roborio-(\d+)-frc(?:\.(?:local|lan))?$", hostmod)
                if m:
                    team = int(m.group(1))

    if team:
        logger.info("Finding robot for team %s", team)
        finder = RobotFinder(
            ("10.%d.%d.2" % (team // 100, team % 100), False),
            ("roboRIO-%d-FRC.local" % team, True),
            ("172.22.11.2", False),  # USB
            ("roboRIO-%d-FRC" % team, True),  # default DNS
            ("roboRIO-%d-FRC.lan" % team, True),
            ("roboRIO-%d-FRC.frc-field.local" % team, True),  # practice field mDNS
        )
        hostname = finder.find()
        no_resolve = True
        if not hostname:
            raise Error("Could not find team %s robot" % team)

    if not no_resolve:
        hostname = _resolve_addr(hostname)

    logger.info("Connecting to robot via SSH at %s", hostname)

    return SshController(hostname, username, password)
