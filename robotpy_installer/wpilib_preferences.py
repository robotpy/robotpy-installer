import dataclasses
import logging
import json
import pathlib
import typing


logger = logging.getLogger("robotpy.installer")


def _wpilib_preferences_json_path(project_path: pathlib.Path):
    return project_path / ".wpilib" / "wpilib_preferences.json"


@dataclasses.dataclass
class WPILibPreferencesJson:
    #: current language
    currentLanguage: typing.Optional[str] = None
    #: project year
    projectYear: typing.Optional[str] = None
    #: team number
    teamNumber: typing.Optional[int] = None
    #: robot hostname -- should never need to specify this
    robotHostname: typing.Optional[str] = None

    def write(self, project_path: pathlib.Path):
        """
        Writes this wpilib_preferences.json file to disk

        :param project_path: Path to robot project
        """
        data = dataclasses.asdict(self)
        data = {k: v for k, v in data.items() if v is not None}

        fname = _wpilib_preferences_json_path(project_path)
        fname.parent.mkdir(parents=True, exist_ok=True)

        with open(fname, "w") as fp:
            json.dump(data, fp)

        logger.info("Settings stored at %s", fname)


def load(project_path: pathlib.Path) -> WPILibPreferencesJson:
    """
    Reads the project's wpilib_preferences.json from disk. Raises FileNotFoundError
    if not present.

    :param project_path: Path to robot project
    """

    wpilib_preferences_json = _wpilib_preferences_json_path(project_path)

    with open(wpilib_preferences_json, "r") as fp:
        data = json.load(fp)

    logger.info("Settings loaded from %s", wpilib_preferences_json)

    currentLanguage = data.get("currentLanguage", None)
    if currentLanguage is not None:
        currentLanguage = str(currentLanguage)

    projectYear = data.get("projectYear", None)
    if projectYear is not None:
        projectYear = str(projectYear)

    teamNumber = data.get("teamNumber", None)
    if teamNumber is not None:
        try:
            teamNumber = int(teamNumber)
        except ValueError:
            raise ValueError(
                f"{wpilib_preferences_json}: teamNumber must be an integer (got {teamNumber!r})"
            ) from None

    robotHostname = data.get("robotHostname", None)
    if robotHostname is not None:
        robotHostname = str(robotHostname)

    return WPILibPreferencesJson(
        currentLanguage,
        projectYear,
        teamNumber,
        robotHostname,
    )
