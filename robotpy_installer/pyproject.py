import dataclasses
from importlib.metadata import metadata, PackageNotFoundError
import inspect
import pathlib
import typing

from packaging.requirements import Requirement
from packaging.version import Version, InvalidVersion
import tomli

from . import pypackages
from .pypackages import Packages, Env
from .errors import Error


class PyprojectError(Error):
    pass


class NoRobotpyError(PyprojectError):
    pass


def toml_path(project_path: pathlib.Path):
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

    #: Version of robotpy that is depended on
    robotpy_version: Version

    robotpy_extras: typing.List[str] = dataclasses.field(default_factory=list)

    #: Requirement for the robotpy meta package -- all RobotPy projects must
    #: depend on it
    @property
    def robotpy_requires(self) -> Requirement:
        if self.robotpy_extras:
            extras = f"[{','.join(self.robotpy_extras)}]"
        else:
            extras = ""
        return Requirement(f"robotpy{extras}=={self.robotpy_version}")

    #: User's custom requirements
    requires: typing.List[Requirement] = dataclasses.field(default_factory=list)

    def are_requirements_met(
        self,
        packages: Packages,
        env: Env,
        extra_resolver: pypackages.ExtraResolver,
    ) -> typing.Tuple[bool, typing.List[str]]:
        """
        Determines if the set of packages meets the requirements specified by
        this project
        """
        reqs = self.get_install_reqs()
        assert reqs and reqs[0].name == "robotpy"
        robotpy_req = reqs[0]

        # Extra requirements from the extra resolver
        reqs.extend(extra_resolver(robotpy_req, env))

        return pypackages.are_requirements_met(reqs, packages, env)

    def are_local_requirements_met(
        self,
    ) -> typing.Tuple[bool, typing.List[str]]:
        """
        Determines if the locally installed packages meets the requirements
        specified by this project
        """

        return self.are_requirements_met(
            pypackages.get_local_packages(), {}, pypackages.extra_resolver_local
        )

    def get_install_reqs(self) -> typing.List[Requirement]:
        return [self.robotpy_requires] + self.requires

    def get_install_list(self) -> typing.List[str]:
        return list(map(str, self.get_install_reqs()))


def robotpy_installed_version() -> str:
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

    robotpy_version = robotpy_installed_version()

    provides_extra = metadata("robotpy").get_all("Provides-Extra")
    if not provides_extra:
        extras = ""
    else:
        extras = "\n    # ".join(f'"{extra}",' for extra in sorted(provides_extra))

    content = inspect.cleandoc(
        f"""
            
            #
            # Use this configuration file to control what RobotPy packages are installed
            # on your RoboRIO
            #

            [tool.robotpy]

            # Version of robotpy this project depends on
            robotpy_version = "{robotpy_version}"
            
            # Which extra RobotPy components should be installed
            # -> equivalent to `pip install robotpy[extra1, ...]
            robotpy_extras = [
                # ##EXTRAS##
            ]

            # Other pip packages to install
            requires = []

        """
    )

    content += "\n"
    content = content.replace("##EXTRAS##", extras)

    with open(toml_path(project_path), "w") as fp:
        fp.write(content)


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

    pyproject_path = toml_path(project_path)
    if not pyproject_path.exists():
        if default_if_missing:
            return RobotPyProjectToml(
                robotpy_version=Version(robotpy_installed_version())
            )
        if write_if_missing:
            write_default_pyproject(project_path)

    with open(pyproject_path, "rb") as fp:
        data = tomli.load(fp)

    return _load(str(pyproject_path), data)


def loads(content: str):
    data = tomli.loads(content)
    return _load("<string>", data)


def _load(
    pyproject_path: str, data: typing.Dict[str, typing.Any]
) -> RobotPyProjectToml:
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

    requires_any = robotpy_data.get("requires")
    if isinstance(requires_any, list):
        requires = []
        for req in requires_any:
            requires.append(Requirement(req))
    elif requires_any:
        requires = [Requirement(str(requires_any))]
    else:
        requires = []

    return RobotPyProjectToml(
        robotpy_version=robotpy_version,
        robotpy_extras=robotpy_extras,
        requires=requires,
    )
