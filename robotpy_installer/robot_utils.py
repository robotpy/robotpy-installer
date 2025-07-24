import json
import logging
import typing

from .sshcontroller import SshController

logger = logging.getLogger("robotpy.installer")


ssh_username = "systemcore"
ssh_password = "systemcore"

java_jars = "/home/systemcore/*.jar"
cpp_exe = "/home/systemcore/frcUserProgram"
robot_command = "/home/systemcore/robotCommand"
static_deploy = "/home/systemcore/deploy"

kill_robot_cmd = f"sudo systemctl stop robot"
kill_script_content: typing.Optional[bytes] = None


def uninstall_cpp_java(ssh: SshController):
    """
    Frees up disk space by removing FRC C++/Java programs. This runs as lvuser or admin.

    :returns: True if success, False if uninstall_cpp_java_admin needs to be ran
    """

    logger.info("Clearing FRC C++/Java user programs if present")

    rm_paths = (java_jars, cpp_exe, robot_command)

    ssh.exec_bash(
        "set -x",
        # Kill code only if java jar present
        f"[ ! -f {java_jars} ] || {kill_robot_cmd}",
        # Kill code only if cpp exe present
        f"[ ! -f {cpp_exe} ] || {kill_robot_cmd}",
        f"rm -rf {' '.join(rm_paths)}",
    )

    # # Check if admin pieces need to run
    # result = ssh.exec_bash(
    #     '[ -z "$(opkg list-installed frc*-openjdk-*)" ]',
    #     f'[ ! -d {third_party_libs} ] || [ -z "$(ls /usr/local/frc/third-party/lib)" ]',
    #     # This is copied with admin privs, can't delete as lvuser
    #     f"[ ! -d {static_deploy} ]",
    # )
    # return result.returncode == 0


# def uninstall_cpp_java_admin(ssh: SshController):
#     """
#     Frees up disk space by removing FRC C++/Java programs. Fails if not ran as admin.
#     """

#     logger.info("Clearing FRC C++/Java program support")

# rm_paths = (third_party_libs, static_deploy)

# ssh.exec_bash(
#     # Remove java ipk
#     'opkg remove "frc*-openjdk*"',
#     # Remove third party libs not used by RobotPy
#     f"rm -rf {' '.join(rm_paths)}",
#     bash_opts="ex",
#     print_output=True,
#     check=True,
# )


def get_robot_py_packages(ssh: SshController) -> typing.Dict[str, str]:
    if not ssh.sftp_remote_file_exists("/home/systemcore/venv/bin/python3"):
        return {}

    # Use importlib.metadata instead of pip because it's way faster than pip
    result = ssh.exec_cmd(
        "/home/systemcore/venv/bin/python3 -c "
        "'from importlib.metadata import distributions;"
        "import json; import sys; "
        "json.dump({dist.name: dist.version for dist in distributions()},sys.stdout)'",
        get_output=True,
    )
    assert result.stdout is not None
    d = json.loads(result.stdout)
    # sometimes distributions.name or version is None
    return {k: v for k, v in d.items() if k and v}
