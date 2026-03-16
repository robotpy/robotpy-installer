import inspect
import pathlib
import typing

from robotpy_installer import pyproject, pypackages
from robotpy_installer.installer import _WPILIB_YEAR as YEAR

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def load_project(content: str) -> pyproject.RobotPyProjectToml:
    return pyproject.loads(inspect.cleandoc(content))


def null_resolver(req: Requirement, env: pypackages.Env) -> typing.List[Requirement]:
    return []


def test_ok():
    project = load_project(f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
    """)
    installed = pypackages.make_packages({"robotpy": f"{YEAR}.1.1.2"})
    assert project.are_requirements_met(
        installed, pypackages.robot_env(), null_resolver
    ) == (
        True,
        [],
    )


def test_older_fail():
    project = load_project(f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
    """)
    installed = pypackages.make_packages({"robotpy": f"{YEAR}.1.1.0"})
    assert project.are_requirements_met(
        installed, pypackages.robot_env(), null_resolver
    ) == (
        False,
        [f"robotpy=={YEAR}.1.1.2 (found {YEAR}.1.1.0)"],
    )


def test_older_and_newer_fail():
    project = load_project(f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
    """)
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
    project = load_project(f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
        requires = [
            "robotpy-commands-v2"
        ]
    """)

    installed = pypackages.make_packages(
        {"robotpy": f"{YEAR}.1.1.2", "robotpy-commands-v2": f"{YEAR}.0.0b4"}
    )

    assert project.are_requirements_met(
        installed, pypackages.robot_env(), null_resolver
    ) == (
        True,
        [],
    )


# def test_env_marker():
#     project = load_project(
#         f"""
#         [tool.robotpy]
#         robotpy_version = "{YEAR}.1.1.2"
#         requires = [
#             "robotpy-opencv; platform_machine == 'roborio'",
#             "opencv-python; platform_machine != 'roborio'"
#         ]
#     """
#     )

#     installed = pypackages.make_packages(
#         {"robotpy": f"{YEAR}.1.1.2", "robotpy-opencv": f"{YEAR}.0.0"}
#     )

#     assert project.are_requirements_met(
#         installed, pypackages.robot_env(), null_resolver
#     ) == (
#         True,
#         [],
#     )


def test_get_deploy_list_resolves_direct_url_to_wheel():
    project = load_project(f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
        requires = [
            "frc3484 @ git+https://github.com/FRC-Team3484/FRC3484_Lib_Python.git@main"
        ]
    """)

    wheel = pathlib.Path("/tmp/frc3484-1.2.3-py3-none-any.whl")
    cached = {
        canonicalize_name("robotpy"): [
            pypackages.CacheVersion(f"{YEAR}.1.1.2", pathlib.Path("/tmp/robotpy.whl"))
        ],
        canonicalize_name("frc3484"): [
            pypackages.CacheVersion("1.2.3", pathlib.Path("/tmp/frc3484-1.2.3.zip")),
            pypackages.CacheVersion("1.2.3", wheel),
        ],
    }

    assert project.get_deploy_list(cached) == [f"robotpy=={YEAR}.1.1.2", str(wheel)]


def test_get_deploy_list_requires_wheel_for_direct_url():
    project = load_project(f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
        requires = [
            "frc3484 @ git+https://github.com/FRC-Team3484/FRC3484_Lib_Python.git@main"
        ]
    """)

    cached = {
        canonicalize_name("robotpy"): [
            pypackages.CacheVersion(f"{YEAR}.1.1.2", pathlib.Path("/tmp/robotpy.whl"))
        ],
        canonicalize_name("frc3484"): [
            pypackages.CacheVersion("1.2.3", pathlib.Path("/tmp/frc3484-1.2.3.tar.gz"))
        ],
    }

    try:
        project.get_deploy_list(cached)
        assert False
    except KeyError as e:
        assert "not as a wheel" in str(e)
