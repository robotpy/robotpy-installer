import io
import logging
import re
import os
from os.path import exists, join, expanduser, split as splitpath
from pathlib import Path, PurePath, PurePosixPath
import socket
import sys
import typing


import paramiko

from .errors import SshExecError, Error
from .robotfinder import RobotFinder
from .utils import _resolve_addr

from . import wpilib_preferences

logger = logging.getLogger("robotpy.installer")


class SuppressKeyPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        return


class SshExecResult(typing.NamedTuple):
    returncode: int
    stdout: typing.Optional[str]


class SshController:
    """
    Use this to execute commands on a roboRIO in a cross platform manner

    ::

        with SshController(hostname, username, password) as controller:
            controller.exec_cmd("cat /etc/lsb-release", print_output=True)

    """

    def __init__(
        self,
        hostname: str,
        username: str,
        password: str,
        conn: typing.Optional[socket.socket] = None,
    ):
        self.username = username
        self.password = password
        self.hostname = hostname
        self.conn = conn

        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(SuppressKeyPolicy)

    def __enter__(self):
        self.client.connect(
            self.hostname,
            username=self.username,
            password=self.password,
            allow_agent=False,
            look_for_keys=False,
            sock=self.conn,
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

        transport = self.client.get_transport()
        assert transport is not None

        with transport.open_session() as channel:
            channel.set_combine_stderr(True)
            channel.exec_command(cmd)

            with channel.makefile("r") as stdout:
                for line in stdout:
                    if get_output:
                        buffer.write(line)
                    if print_output:
                        try:
                            print(line, end="")
                        except UnicodeEncodeError:
                            eline = line.encode(
                                sys.stdout.encoding, "backslashreplace"
                            ).decode(sys.stdout.encoding)
                            print(eline, end="")

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

    def sftp_fp(self, fp, remote_path):
        sftp = self.client.open_sftp()
        try:
            sftp.putfo(fp, remote_path)
        finally:
            sftp.close()


def ssh_from_cfg(
    project_path: Path,
    main_file: Path,
    username: str,
    password: str,
    robot_or_team: typing.Union[None, str, int] = None,
    no_resolve=False,
):
    try:
        prefs = wpilib_preferences.load(project_path)
        dirty = False
    except FileNotFoundError:
        prefs = wpilib_preferences.WPILibPreferencesJson()
        dirty = True

    if robot_or_team is not None:
        if isinstance(robot_or_team, int):
            prefs.teamNumber = robot_or_team
        else:
            prefs.robotHostname = robot_or_team

    if prefs.teamNumber is None and prefs.robotHostname is None:
        dirty = True

        print("Robot setup (hit enter for default value):")
        response = ""
        while not response:
            response = input("Team number or robot hostname: ")

        try:
            prefs.teamNumber = int(response)
        except ValueError:
            prefs.robotHostname = response

    if dirty:
        # Only write preferences file if this is a robot project
        if main_file.exists():
            prefs.write(project_path)
        else:
            logger.info(
                "-> not saving robot preferences as this isn't a robot project directory"
            )

    team: typing.Optional[int] = prefs.teamNumber
    hostname: typing.Optional[str] = prefs.robotHostname

    # Prefer a hostname if specified
    if hostname:
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

        # Attempt to convert it to a team number, which allows users to
        # benefit from the robot finder
        try:
            team = int(hostname.strip())
        except ValueError:
            if not no_resolve:
                hostmod = hostname.lower().strip()
                m = re.search(r"10.(\d+).(\d+).2", hostmod)
                if m:
                    team = int(m.group(1)) * 100 + int(m.group(2))
                    hostname = None
                else:
                    m = re.match(r"roborio-(\d+)-frc(?:\.(?:local|lan))?$", hostmod)
                    if m:
                        team = int(m.group(1))
                        hostname = None
        else:
            hostname = None

    conn = None

    assert team is not None or hostname is not None

    if hostname is not None:
        if no_resolve:
            conn_hostname = hostname
        else:
            conn_hostname = _resolve_addr(hostname)
    elif team is not None:
        logger.info("Finding robot for team %s", team)
        finder = RobotFinder(
            ("10.%d.%d.2" % (team // 100, team % 100), False),
            ("roboRIO-%d-FRC.local" % team, True),
            ("172.22.11.2", False),  # USB
            ("roboRIO-%d-FRC" % team, True),  # default DNS
            ("roboRIO-%d-FRC.lan" % team, True),
            ("roboRIO-%d-FRC.frc-field.local" % team, True),  # practice field mDNS
        )
        answer = finder.find()
        if not answer:
            raise Error("Could not find team %s robot" % team)

        no_resolve = True
        conn_hostname, conn = answer
    else:
        raise Error("internal logic error")

    logger.info("Connecting to robot via SSH at %s", conn_hostname)

    return SshController(conn_hostname, username, password, conn)
