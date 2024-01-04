import argparse

import pathlib
import json
import sys
import typing

from . import sshcontroller

from .utils import print_err


class DeployInfo:
    """
    Displays information about code deployed to robot.
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

    def run(
        self,
        main_file: pathlib.Path,
        robot: typing.Optional[str],
        team: typing.Optional[int],
        no_resolve: bool,
    ):
        if not main_file.exists():
            print(
                f"ERROR: is this a robot project? {main_file} does not exist",
                file=sys.stderr,
            )
            return 1

        cfg_filename = main_file.parent / ".deploy_cfg"

        hostname_or_team = robot or team

        try:
            with sshcontroller.ssh_from_cfg(
                cfg_filename,
                username="lvuser",
                password="",
                hostname=hostname_or_team,
                no_resolve=no_resolve,
            ) as ssh:
                result = ssh.exec_cmd(
                    (
                        "[ -f /home/lvuser/py/deploy.json ] && "
                        "cat /home/lvuser/py/deploy.json || "
                        "echo {}"
                    ),
                    get_output=True,
                )
                if not result.stdout:
                    print("{}")
                else:
                    data = json.loads(result.stdout)
                    print(json.dumps(data, indent=2, sort_keys=True))

        except sshcontroller.SshExecError as e:
            print_err("ERROR:", str(e))
            return 1

        return 0
