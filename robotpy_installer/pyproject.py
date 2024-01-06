import dataclasses
from importlib.metadata import metadata, PackageNotFoundError
import inspect
import pathlib
import typing

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version, InvalidVersion
import tomli

from .errors import Error


class PyprojectError(Error):
    pass


class NoRobotpyError(PyprojectError):
    pass


def _pyproject_toml_path(project_path: pathlib.Path):
    return project_path / "pyproject.toml"


@dataclasses.dataclass
class RobotPyProjectToml:
    """
    The results of parsing a ``pyproject.toml`` file in a RobotPy project. This
    only parses fields that matter for deploy/install (TODO: this probably
    should live in a single unified place?)

    .. code-block:: toml

        [tool.robotpy]

        # equivalent to `robotpy==2024.0.0b4`
        robotpy_version = "2024.0.0b4"

        # equivalent to `robotpy[cscore, ...]`
        robotpy_extras = ["cscore"]

        # Other pip installable requirement lines
        requires = [
        "numpy"
        ]

    """

    #: Requirement for the robotpy meta package -- all RobotPy projects must
    #: depend on it
    robotpy_requires: Requirement

    #: Requirements for
    requires: typing.List[Requirement] = dataclasses.field(default_factory=list)

    def get_install_list(self) -> typing.List[str]:
        packages = [str(self.robotpy_requires)]
        packages.extend([str(req) for req in self.requires])
        return packages


def robotpy_default_version() -> str:
    # this is a bit weird because this project doesn't depend on robotpy, it's
    # the other way around.. but oh well?
    try:
        return metadata("robotpy")["Version"]
    except PackageNotFoundError:
        raise NoRobotpyError(
            "cannot infer default robotpy package version: robotpy package not installed "
            "(do `pip install robotpy` or create a pyproject.toml)"
        ) from None


def write_default_pyproject(
    project_path: pathlib.Path,
):
    """
    Using the current environment, write a minimal pyproject.toml

    :param project_path: Path to robot project
    """

    robotpy_version = robotpy_default_version()

    with open(_pyproject_toml_path(project_path), "w") as fp:
        fp.write(
            inspect.cleandoc(
                f"""
            
            #
            # Use this configuration file to control what RobotPy packages are installed
            # on your RoboRIO
            #

            [tool.robotpy]

            # Version of robotpy this project depends on
            robotpy_version = "{robotpy_version}"
            
            # Which extras should be installed
            # -> equivalent to `pip install robotpy[extra1, ...]
            robotpy_extras = []

            # Other pip packages to install
            requires = []

        """
            )
            + "\n"
        )


def load(
    project_path: pathlib.Path,
    *,
    write_if_missing: bool = False,
    default_if_missing=False,
) -> RobotPyProjectToml:
    """
    Reads a pyproject.toml file for a RobotPy project. Raises FileNotFoundError
    if the file isn't present

    :param project_path: Path to robot project
    """

    pyproject_path = _pyproject_toml_path(project_path)
    if not pyproject_path.exists():
        if default_if_missing:
            return RobotPyProjectToml(
                robotpy_requires=Requirement(f"robotpy=={robotpy_default_version()}")
            )
        if write_if_missing:
            write_default_pyproject(project_path)

    with open(pyproject_path, "rb") as fp:
        data = tomli.load(fp)

    try:
        robotpy_data = data["tool"]["robotpy"]
        if not isinstance(robotpy_data, dict):
            raise KeyError()
    except KeyError:
        raise PyprojectError(
            f"{pyproject_path} must have [tool.robotpy] section"
        ) from None

    try:
        robotpy_version = Version(robotpy_data["robotpy_version"])
    except KeyError:
        raise PyprojectError(
            f"{pyproject_path} missing required tools.robotpy.robotpy_version"
        ) from None
    except InvalidVersion:
        raise PyprojectError(
            f"{pyproject_path}: tools.robotpy.robotpy_version is not a valid version"
        ) from None

    robotpy_extras_any = robotpy_data.get("robotpy_extras")
    if isinstance(robotpy_extras_any, list):
        robotpy_extras = list(map(str, robotpy_extras_any))
    elif not robotpy_extras_any:
        robotpy_extras = []
    else:
        robotpy_extras = [str(robotpy_extras_any)]

    # Construct the full requirement
    robotpy_pkg = "robotpy"
    if robotpy_extras:
        extras_s = ",".join(robotpy_extras)
        robotpy_pkg = f"robotpy[{extras_s}]"
    robotpy_requires = Requirement(f"{robotpy_pkg}=={robotpy_version}")

    requires_any = robotpy_data.get("requires")
    if isinstance(requires_any, list):
        requires = []
        for req in requires_any:
            requires.append(Requirement(req))
    elif requires_any:
        requires = [Requirement(str(requires_any))]
    else:
        requires = []

    return RobotPyProjectToml(robotpy_requires=robotpy_requires, requires=requires)


def are_requirements_met(
    pp: RobotPyProjectToml, packages: typing.Dict[str, str]
) -> bool:
    pv = {name: Version(v) for name, v in packages.items()}
    for req in [pp.robotpy_requires] + pp.requires:
        req_name = canonicalize_name(req.name)
        met = False
        for pkg, pkg_version in pv.items():
            if pkg == req_name:
                met = pkg_version in req.specifier
                break

        if not met:
            return False

    return True
