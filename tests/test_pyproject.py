import inspect
import pathlib
import typing

import pytest

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


def test_deploy_actions():
    project = load_project(f'''
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"

        [[tool.robotpy.deploy]]
        command = ["python", "-m", "mypy"]

        [[tool.robotpy.deploy]]
        command = ["python", "-m", "pytest"]
        required = false
    ''')

    assert project.deploy == [
        pyproject.DeployAction(["python", "-m", "mypy"], required=True),
        pyproject.DeployAction(["python", "-m", "pytest"], required=False),
    ]


@pytest.mark.parametrize(
    ("deploy", "message"),
    [
        ("deploy = {}", "must be an array"),
        ("deploy = [{}]", "command must be a nonempty array of strings"),
        ('deploy = [{ command = "pytest" }]', "command must be a nonempty array"),
        (
            'deploy = [{ command = ["pytest"], required = "yes" }]',
            "required must be a boolean",
        ),
    ],
)
def test_invalid_deploy_actions(deploy, message):
    with pytest.raises(pyproject.PyprojectError, match=message):
        load_project(f'''
            [tool.robotpy]
            robotpy_version = "{YEAR}.1.1.2"
            {deploy}
        ''')


@pytest.mark.parametrize("requires", [[], ["example==1.2"]])
def test_ignored_version_uses_only_explicit_requirements(requires):
    project = load_project(f"""
        [tool.robotpy]
        robotpy_version = "ignored"
        components = ["cscore"]
        requires = {requires!r}
    """)

    assert project.robotpy_version is None
    assert project.get_install_list() == requires
    assert project.get_deploy_list({}) == requires

    def unexpected_resolver(req, env):
        pytest.fail("ignored RobotPy must not resolve components")

    installed = pypackages.make_packages({"example": "1.2"})
    assert project.are_requirements_met(
        installed, pypackages.robot_env(), unexpected_resolver
    ) == (True, [])
    # Checking requirements must not mutate the explicit requirements.
    assert project.get_install_list() == requires


def test_ignored_version_still_checks_explicit_requirements():
    project = load_project("""
        [tool.robotpy]
        robotpy_version = "ignored"
        requires = ["example==1.2"]
    """)

    assert project.are_requirements_met({}, {}, null_resolver) == (
        False,
        ["example==1.2 (not found)"],
    )


@pytest.mark.parametrize("version", ["invalid", "Ignored", "2024.1.0"])
def test_invalid_or_unsupported_version_is_rejected(version):
    with pytest.raises(pyproject.PyprojectError):
        load_project(f"""
            [tool.robotpy]
            robotpy_version = "{version}"
        """)


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


def test_relative_file_url_resolved_against_project(tmp_path):
    lib_dir = tmp_path / "lib" / "bread"
    lib_dir.mkdir(parents=True)

    project_dir = tmp_path / "robots" / "template"
    project_dir.mkdir(parents=True)

    content = inspect.cleandoc(f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
        requires = [
            "bread @ file://../../lib/bread",
        ]
    """)

    project = pyproject.loads(content, base_path=project_dir)

    assert len(project.requires) == 1
    bread = project.requires[0]
    assert bread.url == lib_dir.resolve().as_uri()


def test_relative_file_url_with_dot_segment(tmp_path):
    lib_dir = tmp_path / "lib" / "bread"
    lib_dir.mkdir(parents=True)

    project_dir = tmp_path / "robots" / "template"
    project_dir.mkdir(parents=True)

    # "file://./foo" form — netloc captures ".", which pip would reject.
    content = inspect.cleandoc(f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
        requires = [
            "bread @ file://./../../lib/bread",
        ]
    """)

    project = pyproject.loads(content, base_path=project_dir)
    assert project.requires[0].url == lib_dir.resolve().as_uri()


def test_absolute_file_url_left_alone(tmp_path):
    lib_dir = tmp_path / "lib" / "bread"
    lib_dir.mkdir(parents=True)
    abs_uri = lib_dir.as_uri()

    content = inspect.cleandoc(f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
        requires = [
            "bread @ {abs_uri}",
        ]
    """)

    project = pyproject.loads(content, base_path=tmp_path)
    assert project.requires[0].url == abs_uri


def test_non_file_url_left_alone(tmp_path):
    git_url = "git+https://github.com/FRC-Team3484/FRC3484_Lib_Python.git@main"

    content = inspect.cleandoc(f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
        requires = [
            "frc3484 @ {git_url}",
        ]
    """)

    project = pyproject.loads(content, base_path=tmp_path)
    assert project.requires[0].url == git_url


def test_loads_without_base_path_preserves_url():
    content = inspect.cleandoc(f"""
        [tool.robotpy]
        robotpy_version = "{YEAR}.1.1.2"
        requires = [
            "bread @ file://../../lib/bread",
        ]
    """)

    project = pyproject.loads(content)
    assert project.requires[0].url == "file://../../lib/bread"
