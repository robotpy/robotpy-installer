from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from robotpy_installer import _pipstub
from robotpy_installer.installer import (
    _ROBOTPY_MANYLINUX_MAX,
    _ROBOTPY_MANYLINUX_MIN,
    _ROBOTPY_PYTHON_PLATFORM,
    _ROBOTPY_PYTHON_VERSION_TUPLE,
    _ROBOTPY_PYTHON_VERSION_TUPLE_FULL,
    _PYTHON_PKG,
)
from robotpy_installer.pypackages import (
    CacheVersion,
    are_requirements_met,
    get_cache_req_download_path,
    robot_env,
)


def test_prerelease_satisfies_requirement_without_mutating_specifier():
    requirement = Requirement("example>=1")
    packages = {canonicalize_name("example"): [Version("1.1b1")]}

    assert are_requirements_met([requirement], packages, {}) == (True, [])
    assert requirement.specifier.prereleases is None


def test_prerelease_wheel_matches_cache_requirement_without_mutation():
    requirement = Requirement("example>=1")
    wheel = Path("example-1.1b1-py3-none-any.whl")
    packages = {canonicalize_name("example"): [CacheVersion("1.1b1", wheel)]}

    assert get_cache_req_download_path(requirement, packages) == wheel
    assert requirement.specifier.prereleases is None


def test_robot_env_matches_robotpy_python_version_tuple():
    robotpy_python_version = ".".join(map(str, _ROBOTPY_PYTHON_VERSION_TUPLE))
    robotpy_python_full_version = ".".join(map(str, _ROBOTPY_PYTHON_VERSION_TUPLE_FULL))

    env = robot_env()

    assert env["python_version"] == robotpy_python_version
    assert env["python_full_version"] == robotpy_python_full_version
    assert env["implementation_version"] == robotpy_python_full_version


def test_python_pkg_uses_robotpy_python_full_version():
    robotpy_python_full_version = ".".join(map(str, _ROBOTPY_PYTHON_VERSION_TUPLE_FULL))

    assert f"/cpython-{robotpy_python_full_version}+" in _PYTHON_PKG


def test_pipstub_matches_installer_robot_python_constants():
    assert _pipstub._ROBOTPY_PYTHON_PLATFORM == _ROBOTPY_PYTHON_PLATFORM
    assert (
        _pipstub._ROBOTPY_PYTHON_VERSION_TUPLE_FULL
        == _ROBOTPY_PYTHON_VERSION_TUPLE_FULL
    )
    assert _pipstub._ROBOTPY_MANYLINUX_MIN == _ROBOTPY_MANYLINUX_MIN
    assert _pipstub._ROBOTPY_MANYLINUX_MAX == _ROBOTPY_MANYLINUX_MAX
