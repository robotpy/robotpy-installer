import inspect

from robotpy_installer import pyproject, pypackages


def load_project(content: str) -> pyproject.RobotPyProjectToml:
    return pyproject.loads(inspect.cleandoc(content))


def test_ok():
    project = load_project(
        """
        [tool.robotpy]
        robotpy_version = "2024.1.1.2"
    """
    )
    installed = pypackages.make_packages({"robotpy": "2024.1.1.2"})
    assert project.are_requirements_met(installed, pypackages.roborio_env()) == (
        True,
        [],
    )


def test_older_fail():
    project = load_project(
        """
        [tool.robotpy]
        robotpy_version = "2024.1.1.2"
    """
    )
    installed = pypackages.make_packages({"robotpy": "2024.1.1.0"})
    assert project.are_requirements_met(installed, pypackages.roborio_env()) == (
        False,
        ["robotpy==2024.1.1.2 (found 2024.1.1.0)"],
    )


def test_older_and_newer_fail():
    project = load_project(
        """
        [tool.robotpy]
        robotpy_version = "2024.1.1.2"
    """
    )
    installed = pypackages.make_packages({"robotpy": ["2024.1.1.0", "2024.1.1.4"]})
    assert project.are_requirements_met(installed, pypackages.roborio_env()) == (
        False,
        ["robotpy==2024.1.1.2 (found 2024.1.1.0, 2024.1.1.4)"],
    )


def test_beta_empty_req():
    project = load_project(
        """
        [tool.robotpy]
        robotpy_version = "2024.1.1.2"
        requires = [
            "robotpy-commands-v2"
        ]
    """
    )

    installed = pypackages.make_packages(
        {"robotpy": "2024.1.1.2", "robotpy-commands-v2": "2024.0.0b4"}
    )

    assert project.are_requirements_met(installed, pypackages.roborio_env()) == (
        True,
        [],
    )


def test_env_marker():
    project = load_project(
        """
        [tool.robotpy]
        robotpy_version = "2024.1.1.2"
        requires = [
            "robotpy-opencv; platform_machine == 'roborio'",
            "opencv-python; platform_machine != 'roborio'"
        ]
    """
    )

    installed = pypackages.make_packages(
        {"robotpy": "2024.1.1.2", "robotpy-opencv": "2024.0.0"}
    )

    assert project.are_requirements_met(installed, pypackages.roborio_env()) == (
        True,
        [],
    )
