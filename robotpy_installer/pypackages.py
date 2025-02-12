"""
Various python packaging related logic
"""

from importlib.metadata import distributions, metadata, PackageNotFoundError
import pathlib
import typing
import zipfile

from packaging.metadata import Metadata
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

ExtraResolver = typing.Callable[[Requirement, Env], typing.List[Requirement]]


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
        req.specifier.prereleases = True

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


def evaluate_extras_markers(
    reqs: typing.List[Requirement], env: Env, extras: typing.Iterable[str]
) -> typing.List[Requirement]:
    env = env.copy()
    matched = []
    for req in reqs:
        for extra in extras:
            env["extra"] = extra
            if req.marker is None or req.marker.evaluate(env):
                # marker is no longer needed
                req.marker = None
                matched.append(req)
                break
    return matched


def extra_resolver_local(req: Requirement, env: Env) -> typing.List[Requirement]:
    """
    Given a requirement, resolves its extras using the version of the
    requirement installed locally. Ignores its markers.

    Fails silently.
    """
    if not req.extras:
        return []

    try:
        m = metadata(req.name)
    except PackageNotFoundError:
        return []

    extra_reqs = []
    requires_dist = m.get_all("Requires-Dist")
    if requires_dist:
        extra_reqs = evaluate_extras_markers(
            [Requirement(r) for r in requires_dist], env, req.extras
        )

    return extra_reqs


def make_cache_extra_resolver(packages: Packages) -> ExtraResolver:
    """
    :param packages: The list of packages in the cache as returned by
                     get_pip_cache_packages
    """

    def _resolver(req: Requirement, env: Env) -> typing.List[Requirement]:
        if not req.extras:
            return []

        env = env.copy()
        env["extra"] = ",".join(req.extras)

        # Find the requirement
        creqs = packages.get(canonicalize_name(req.name))
        if creqs is None:
            raise KeyError(f"{req} not downloaded in cache (did you do a sync?)")

        req.specifier.prereleases = True
        for creq in sorted(creqs, reverse=True):
            if req.specifier is None or creq in req.specifier:
                break
        else:
            raise KeyError(f"{req} not downloaded in cache (did you do a sync?)")

        if not isinstance(creq, CacheVersion):
            raise ValueError("internal error")

        m = metadata_from_wheel(creq.file_path)
        if m.requires_dist:
            return evaluate_extras_markers(m.requires_dist, env, req.extras)

        return []

    return _resolver


def get_local_packages() -> Packages:
    """
    Iterates over locally installed packages and returns dict of versions
    """
    return {
        canonicalize_name(dist.metadata["Name"]): [Version(dist.version)]
        for dist in distributions()
        if dist.metadata["Name"]
    }


class CacheVersion(Version):
    def __init__(self, version: str, file_path: pathlib.Path) -> None:
        super().__init__(version)
        self.file_path = file_path


def get_pip_cache_packages(
    cache_root: pathlib.Path,
) -> Packages:
    """
    Iterates over the pip cache and returns dict of packages
    """

    packages: Packages = {}

    for f in (cache_root / "pip_cache").iterdir():
        if f.suffix == ".whl":
            try:
                name, version, _, _ = parse_wheel_filename(f.name)
                packages.setdefault(name, []).append(CacheVersion(str(version), f))
            except InvalidWheelFilename:
                pass
        elif f.suffix in (".gz", ".zip"):
            try:
                name, version = parse_sdist_filename(f.name)
                packages.setdefault(name, []).append(CacheVersion(str(version), f))
            except InvalidSdistFilename:
                pass

    return packages


def make_packages(
    packages: typing.Mapping[str, typing.Union[typing.List[str], str]],
) -> Packages:
    """
    For unit testing
    """
    return {
        canonicalize_name(name): (
            [Version(version)]
            if isinstance(version, str)
            else [Version(v) for v in version]
        )
        for name, version in packages.items()
    }


def metadata_from_wheel(whl_path: pathlib.Path) -> Metadata:
    """
    Retrieves the metadata from a wheel file
    """
    name, version, _, _ = parse_wheel_filename(whl_path.name)
    with zipfile.ZipFile(whl_path) as zfp:
        m = zfp.read(f"{name}-{version}.dist-info/METADATA")

    return Metadata.from_email(m, validate=False)


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
