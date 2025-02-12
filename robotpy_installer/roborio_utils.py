import hashlib
import io
import json
import logging
import pathlib
import typing

from .sshcontroller import SshController

logger = logging.getLogger("robotpy.installer")


java_jars = "/home/lvuser/*.jar"
cpp_exe = "/home/lvuser/frcUserProgram"
robot_command = "/home/lvuser/robotCommand"
static_deploy = "/home/lvuser/deploy"

third_party_libs = "/usr/local/frc/third-party/lib"

kill_robot_script = "/usr/local/frc/bin/frcKillRobot.sh"
kill_robot_cmd = f"{kill_robot_script} -t"
kill_script_content: typing.Optional[bytes] = None


def uninstall_cpp_java_lvuser(ssh: SshController) -> bool:
    """
    Frees up disk space by removing FRC C++/Java programs. This runs as lvuser or admin.

    :returns: True if success, False if uninstall_cpp_java_admin needs to be ran
    """

    logger.info("Clearing FRC C++/Java user programs if present")

    rm_paths = (java_jars, cpp_exe, robot_command)

    ssh.exec_bash(
        ". /etc/profile.d/frc-path.sh",
        ". /etc/profile.d/natinst-path.sh",
        "set -x",
        # Kill code only if java jar present
        f"[ ! -f {java_jars} ] || {kill_robot_cmd}",
        # Kill code only if cpp exe present
        f"[ ! -f {cpp_exe} ] || {kill_robot_cmd}",
        f"rm -rf {' '.join(rm_paths)}",
    )

    # Check if admin pieces need to run
    result = ssh.exec_bash(
        '[ -z "$(opkg list-installed frc*-openjdk-*)" ]',
        f'[ ! -d {third_party_libs} ] || [ -z "$(ls /usr/local/frc/third-party/lib)" ]',
        # This is copied with admin privs, can't delete as lvuser
        f"[ ! -d {static_deploy} ]",
    )
    return result.returncode == 0


def uninstall_cpp_java_admin(ssh: SshController):
    """
    Frees up disk space by removing FRC C++/Java programs. Fails if not ran as admin.
    """

    logger.info("Clearing FRC C++/Java program support")

    rm_paths = (third_party_libs, static_deploy)

    ssh.exec_bash(
        # Remove java ipk
        'opkg remove "frc*-openjdk*"',
        # Remove third party libs not used by RobotPy
        f"rm -rf {' '.join(rm_paths)}",
        bash_opts="ex",
        print_output=True,
        check=True,
    )


def get_rio_py_packages(ssh: SshController) -> typing.Dict[str, str]:
    # Use importlib.metadata instead of pip because it's way faster than pip
    result = ssh.exec_cmd(
        "/usr/local/bin/python3 -c "
        "'from importlib.metadata import distributions;"
        "import json; import sys; "
        "json.dump({dist.name: dist.version for dist in distributions()},sys.stdout)'",
        get_output=True,
    )
    assert result.stdout is not None
    d = json.loads(result.stdout)
    # sometimes distributions.name or version is None
    return {k: v for k, v in d.items() if k and v}


def get_kill_script() -> bytes:
    global kill_script_content
    if kill_script_content is None:
        with open(pathlib.Path(__file__).parent / "frcKillRobot.sh", "rb") as fp:
            kill_script_content = fp.read()

        # On Windows this sometimes has \r\n, which will fail on the RIO
        kill_script_content = kill_script_content.replace(b"\r\n", b"\n")

    return kill_script_content


def check_kill_script(ssh: SshController) -> bool:
    ks: bytes = get_kill_script()
    ks_hash = hashlib.md5(ks).hexdigest()
    output = ssh.check_output(f"md5sum {kill_robot_script}")
    return output.split(" ", 1)[0] == ks_hash


def update_kill_script(ssh: SshController):
    logger.info("Updating %s", kill_robot_script)
    fp = io.BytesIO(get_kill_script())
    ssh.sftp_fp(fp, kill_robot_script)
