import inspect
import typing

from robotpy_installer import pyproject, pypackages
from robotpy_installer.installer import _WPILIB_YEAR as YEAR

from packaging.requirements import Requirement


def load_project(content: str) -> pyproject.RobotPyProjectToml:
    return pyproject.loads(inspect.cleandoc(content))


def null_resolver(req: Requirement, env: pypackages.Env) -> typing.List[Requirement]:
    return []


def test_ok():
    project = load_project(
        f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
    """
    )
    installed = pypackages.make_packages({"robotpy": f"{YEAR}.1.1.2"})
    assert project.are_requirements_met(
        installed, pypackages.robot_env(), null_resolver
    ) == (
        True,
        [],
    )


def test_older_fail():
    project = load_project(
        f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
    """
    )
    installed = pypackages.make_packages({"robotpy": f"{YEAR}.1.1.0"})
    assert project.are_requirements_met(
        installed, pypackages.robot_env(), null_resolver
    ) == (
        False,
        [f"robotpy=={YEAR}.1.1.2 (found {YEAR}.1.1.0)"],
    )


def test_older_and_newer_fail():
    project = load_project(
        f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
    """
    )
    installed = pypackages.make_packages(
        {"robotpy": [f"{YEAR}.1.1.0", f"{YEAR}.1.1.4"]}
    )
    assert project.are_requirements_met(
        installed, pypackages.robot_env(), null_resolver
    ) == (
        False,
        [f"robotpy=={YEAR}.1.1.2 (found {YEAR}.1.1.0, {YEAR}.1.1.4)"],
    )


def test_beta_empty_req():
    project = load_project(
        f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
        requires = [
            "robotpy-commands-v2"
        ]
    """
    )

    installed = pypackages.make_packages(
        {"robotpy": f"{YEAR}.1.1.2", "robotpy-commands-v2": f"{YEAR}.0.0b4"}
    )

    assert project.are_requirements_met(
        installed, pypackages.robot_env(), null_resolver
    ) == (
        True,
        [],
    )


def test_env_marker():
    project = load_project(
        f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
        requires = [
            "robotpy-opencv; platform_machine == 'roborio'",
            "opencv-python; platform_machine != 'roborio'"
        ]
    """
    )

    installed = pypackages.make_packages(
        {"robotpy": f"{YEAR}.1.1.2", "robotpy-opencv": f"{YEAR}.0.0"}
    )

    assert project.are_requirements_met(
        installed, pypackages.robot_env(), null_resolver
    ) == (
        True,
        [],
    )
