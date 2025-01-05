import dataclasses
from importlib.metadata import metadata, PackageNotFoundError
import inspect
import pathlib
import typing

from packaging.requirements import Requirement
from packaging.version import Version, InvalidVersion
import tomli
import tomlkit

from . import installer
from . import pypackages
from .pypackages import Packages, Env
from .errors import Error


class PyprojectError(Error):
    pass


class NoRobotpyError(PyprojectError):
    pass


class UnsupportedRobotpyVersion(PyprojectError):
    pass


def toml_path(project_path: pathlib.Path):
    return project_path / "pyproject.toml"

def gitignore_path(project_path: pathlib.Path):
    return project_path / ".gitignore"

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

    ignore_content = inspect.cleandoc("""
    # Executables
    *.exe
    *.out
    *.app

    # Log file
    *.log

    # Package Files #
    *.jar
    *.war
    *.nar
    *.ear
    *.zip
    *.tar.gz
    *.rar

    # virtual machine crash logs, see http://www.java.com/en/download/help/error_hotspot.xml
    hs_err_pid*

    ### Linux ###
    *~

    # temporary files which can be created if a process still has a handle open of a deleted file
    .fuse_hidden*

    # KDE directory preferences
    .directory

    # Linux trash folder which might appear on any partition or disk
    .Trash-*

    # .nfs files are created when an open file is removed but is still being accessed
    .nfs*

    ### macOS ###
    # General
    .DS_Store
    .AppleDouble
    .LSOverride

    # Icon must end with two \r
    Icon

    # Thumbnails
    ._*

    # Files that might appear in the root of a volume
    .DocumentRevisions-V100
    .fseventsd
    .Spotlight-V100
    .TemporaryItems
    .Trashes
    .VolumeIcon.icns
    .com.apple.timemachine.donotpresent

    # Directories potentially created on remote AFP share
    .AppleDB
    .AppleDesktop
    Network Trash Folder
    Temporary Items
    .apdisk

    ### VisualStudioCode ###
    .vscode/*
    !.vscode/settings.json
    !.vscode/tasks.json
    !.vscode/launch.json
    !.vscode/extensions.json

    ### Windows ###
    # Windows thumbnail cache files
    Thumbs.db
    ehthumbs.db
    ehthumbs_vista.db

    # Dump file
    *.stackdump

    # Folder config file
    [Dd]esktop.ini

    # Recycle Bin used on file shares
    $RECYCLE.BIN/

    # Windows Installer files
    *.cab
    *.msi
    *.msix
    *.msm
    *.msp

    # Windows shortcuts
    *.lnk

    ### Gradle ###
    .gradle
    /build/

    # Ignore Gradle GUI config
    gradle-app.setting

    # Avoid ignoring Gradle wrapper jar file (.jar files are usually ignored)
    !gradle-wrapper.jar

    # Cache of project
    .gradletasknamecache

    # # Work around https://youtrack.jetbrains.com/issue/IDEA-116898
    # gradle/wrapper/gradle-wrapper.properties

    # # VS Code Specific Java Settings
    # DO NOT REMOVE .classpath and .project
    .classpath
    .project
    .settings/
    bin/

    # IntelliJ
    *.iml
    *.ipr
    *.iws
    .idea/
    out/

    # Fleet
    .fleet

    # Simulation GUI and other tools window save file
    networktables.json
    simgui.json
    *-window.json

    # Simulation data log directory
    logs/

    # Folder that has CTRE Phoenix Sim device config storage
    ctre_sim/

    # clangd
    /.cache
    compile_commands.json

    # Eclipse generated file for annotation processors
    .factorypath

    # Byte-compiled / optimized / DLL files
    __pycache__/
    *.py[cod]
    *$py.class

    # C extensions
    *.so

    # Distribution / packaging
    .Python
    build/
    develop-eggs/
    dist/
    downloads/
    eggs/
    .eggs/
    lib/
    lib64/
    parts/
    sdist/
    var/
    wheels/
    share/python-wheels/
    *.egg-info/
    .installed.cfg
    *.egg
    MANIFEST

    # PyInstaller
    #  Usually these files are written by a python script from a template
    #  before PyInstaller builds the exe, so as to inject date/other infos into it.
    *.manifest
    *.spec

    # Installer logs
    pip-log.txt
    pip-delete-this-directory.txt

    # Unit test / coverage reports
    htmlcov/
    .tox/
    .nox/
    .coverage
    .coverage.*
    .cache
    nosetests.xml
    coverage.xml
    *.cover
    *.py,cover
    .hypothesis/
    .pytest_cache/
    cover/

    # Translations
    *.mo
    *.pot

    # Django stuff:
    local_settings.py
    db.sqlite3
    db.sqlite3-journal

    # Flask stuff:
    instance/
    .webassets-cache

    # Scrapy stuff:
    .scrapy

    # Sphinx documentation
    docs/_build/

    # PyBuilder
    .pybuilder/
    target/

    # Jupyter Notebook
    .ipynb_checkpoints

    # IPython
    profile_default/
    ipython_config.py

    # pyenv
     .python-version

    # pipenv
    Pipfile.lock

    # UV
    uv.lock

    # poetry
    poetry.lock

    # pdm
    pdm.lock
    .pdm.toml
    .pdm-python
    .pdm-build/

    # PEP 582; used by e.g. github.com/David-OConnor/pyflow and github.com/pdm-project/pdm
    __pypackages__/

    # Celery stuff
    celerybeat-schedule
    celerybeat.pid

    # SageMath parsed files
    *.sage.py

    # Environments
    .env
    .venv
    env/
    venv/
    ENV/
    env.bak/
    venv.bak/

    # Spyder project settings
    .spyderproject
    .spyproject

    # Rope project settings
    .ropeproject

    # mkdocs documentation
    /site

    # mypy
    .mypy_cache/
    .dmypy.json
    dmypy.json

    # Pyre type checker
    .pyre/

    # pytype static type analyzer
    .pytype/

    # Cython debug symbols
    cython_debug/

    # PyPI configuration file
    .pypirc
        """)

    ignore_content += "\n"

    with open(gitignore_path(project_path), "w") as fp:
        fp.write(ignore_content)

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

    supported_year = int(installer._WPILIB_YEAR)
    if robotpy_version.major != supported_year:
        msg = (
            f"Only RobotPy {supported_year}.x is supported by this version "
            f"of robotpy-installer ({pyproject_path} has {robotpy_version})"
        )
        raise UnsupportedRobotpyVersion(msg)

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

def set_robotpy_version(project_path: pathlib.Path, version: Version):
    pyproject_path = toml_path(project_path)
    with open(pyproject_path) as fp:
        data = tomlkit.parse(fp.read())

    try:
        data["tool"]["robotpy"]["robotpy_version"] = str(version)  # type: ignore
    except Exception as e:
        raise ValueError("`pyproject.toml` is not valid") from e

    rawdata = tomlkit.dumps(data)

    with open(pyproject_path, "w") as fp:
        fp.write(rawdata)
