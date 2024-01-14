"""
Various python packaging related logic
"""

from importlib.metadata import distributions
import pathlib
import typing

from packaging.requirements import Requirement
from packaging.utils import (
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
    NormalizedName,
    InvalidSdistFilename,
    InvalidWheelFilename,
)
from packaging.version import Version

#: environment markers needed by Marker.evaluate
Env = typing.Dict[str, str]

Packages = typing.Dict[NormalizedName, typing.List[Version]]


def are_requirements_met(
    requirements: typing.List[Requirement],
    packages: Packages,
    env: Env,
) -> typing.Tuple[bool, typing.List[str]]:
    """
    Given a set of packages and a list of requirements, determine if the packages
    satisfy the list of requirements
    """

    unmet_requirements = []

    for req in requirements:
        # Ignore this requirement if it doesn't apply to the specified
        # environment
        if req.marker and not req.marker.evaluate(env):
            continue

        req_name = canonicalize_name(req.name)

        empty_specifier = str(req.specifier) == ""

        for pkg, pkg_versions in packages.items():
            if pkg == req_name:
                if not empty_specifier:
                    for pkg_version in pkg_versions:
                        if pkg_version in req.specifier:
                            break
                    else:
                        found = ", ".join(map(str, sorted(pkg_versions)))
                        unmet_requirements.append(
                            f"{req.name}{req.specifier} (found {found})"
                        )
                break
        else:
            unmet_requirements.append(f"{req.name}{req.specifier} (not found)")

    return not bool(unmet_requirements), unmet_requirements


def get_local_packages() -> Packages:
    """
    Iterates over locally installed packages and returns dict of versions
    """
    return {
        canonicalize_name(dist.metadata["Name"]): [Version(dist.version)]
        for dist in distributions()
    }


def make_packages(
    packages: typing.Mapping[str, typing.Union[typing.List[str], str]]
) -> Packages:
    """
    For unit testing
    """
    return {
        canonicalize_name(name): [Version(version)]
        if isinstance(version, str)
        else [Version(v) for v in version]
        for name, version in packages.items()
    }


def roborio_env() -> Env:
    """
    For use with ``packaging.marker.Marker.evaluate``
    """
    return {
        "implementation_name": "cpython",
        "implementation_version": "3.12.1",
        "os_name": "posix",
        "platform_machine": "roborio",
        "platform_python_implementation": "CPython",
        "platform_system": "Linux",
        "python_full_version": "3.12.0",
        "python_version": "3.12",
        "sys_platform": "linux",
    }
