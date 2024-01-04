import argparse
import pathlib
import sys
import typing


from os.path import abspath, dirname, join

from . import sshcontroller
from .utils import print_err, yesno


class Undeploy:
    """
    Removes current Python robot code from a RoboRIO
    """

    def __init__(self, parser: argparse.ArgumentParser):
        robot_args = parser.add_mutually_exclusive_group()

        robot_args.add_argument(
            "--robot", default=None, help="Set hostname or IP address of robot"
        )

        robot_args.add_argument(
            "--team", default=None, type=int, help="Set team number to deploy robot for"
        )

        parser.add_argument(
            "--no-resolve",
            action="store_true",
            default=False,
            help="If specified, don't do a DNS lookup, allow ssh et al to do it instead",
        )
        parser.add_argument(
            "--yes",
            "-y",
            action="store_true",
            default=False,
            help="Do it without prompting",
        )

    def run(
        self,
        main_file: pathlib.Path,
        robot: typing.Optional[str],
        team: typing.Optional[int],
        no_resolve: bool,
        yes: bool,
    ):
        if not main_file.exists():
            print(
                f"ERROR: is this a robot project? {main_file} does not exist",
                file=sys.stderr,
            )
            return 1
        cfg_filename = main_file.parent / ".deploy_cfg"

        if not yes:
            if not yesno(
                "This will stop your robot code and delete it from the RoboRIO. Continue?"
            ):
                return 1

        hostname_or_team = robot or team

        try:
            with sshcontroller.ssh_from_cfg(
                cfg_filename,
                username="lvuser",
                password="",
                hostname=hostname_or_team,
                no_resolve=no_resolve,
            ) as ssh:
                # first, turn off the running program
                ssh.exec_cmd("/usr/local/frc/bin/frcKillRobot.sh -t")

                # delete the code
                ssh.exec_cmd("rm -rf /home/lvuser/py")

                # for good measure, delete the start command too
                ssh.exec_cmd(
                    "rm -f /home/lvuser/robotDebugCommand /home/lvuser/robotCommand"
                )

        except sshcontroller.SshExecError as e:
            print_err("ERROR:", str(e))
            return 1

        print("SUCCESS: Files have been successfully wiped!")

        return 0
