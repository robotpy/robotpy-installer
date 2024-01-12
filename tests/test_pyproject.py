import inspect

from robotpy_installer import pyproject


def load_project(content: str) -> pyproject.RobotPyProjectToml:
    return pyproject.loads(inspect.cleandoc(content))


def test_ok():
    project = load_project(
        """
        [tool.robotpy]
        robotpy_version = "2024.1.1.2"
    """
    )
    installed = {"robotpy": "2024.1.1.2"}
    assert pyproject.are_requirements_met(project, installed) == (
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
    installed = {"robotpy": "2024.1.1.0"}
    assert pyproject.are_requirements_met(project, installed) == (
        False,
        ["robotpy==2024.1.1.2 (found 2024.1.1.0)"],
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

    installed = {"robotpy": "2024.1.1.2", "robotpy-commands-v2": "2024.0.0b4"}

    assert pyproject.are_requirements_met(project, installed) == (True, [])
