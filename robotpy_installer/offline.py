"""Creation and passive inspection of offline dependency bundles."""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import venv
import zipapp
from urllib.parse import urlsplit
from pathlib import Path
from typing import Optional, Sequence

from packaging.tags import compatible_tags, cpython_tags, sys_tags
from packaging.utils import parse_wheel_filename

from . import _bundle_format, installer, pyproject
from ._bundle_format import BundleError, artifact_role, validate_bundle
from .installer import RobotpyInstaller
from .version import __version__

# Task 3 supplies this standalone source. Never substitute a nonfunctional launcher.
_LAUNCHER_SOURCE = Path(__file__).with_name("_bundle_main.py")


def _reject_credential_url(url: str) -> None:
    """Reject userinfo in direct requirements without echoing credentials."""
    try:
        unsafe = urlsplit(url).username is not None
    except ValueError:
        unsafe = True
    if unsafe:
        raise BundleError("Invalid or credential-bearing direct requirement URL")


def _stage_local_wheels(
    requirements: Sequence[str],
    destination: Path,
    *,
    project_dir: Path,
    find_links: Optional[Path],
    use_certifi: bool,
) -> Path:
    """Build the complete local closure in a fresh interpreter environment."""
    destination.mkdir(parents=True, exist_ok=True)
    tools = destination.parent / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="robotpy-resolution-") as temporary:
        environment = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True, system_site_packages=False).create(environment)
        python = environment / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        command = [
            str(python),
            "-I",
            "-m",
            "pip",
            "--disable-pip-version-check",
            "--no-cache-dir",
        ]
        options = []
        if find_links is not None:
            options += ["--find-links", str(find_links.resolve())]
        if use_certifi:
            import certifi  # type: ignore[import-not-found]

            options += ["--cert", certifi.where()]
        # Keep acquisition/index configuration, but force a full closure and
        # isolated PEP 517 builds. pip configuration stores store_false options
        # directly: NO_BUILD_ISOLATION=1 means build_isolation=True (not False).
        resolution_env = dict(
            os.environ, PIP_NO_DEPS="0", PIP_NO_BUILD_ISOLATION="1", PIP_USE_PEP517="1"
        )
        # -I prevents PYTHONPATH/user-site leakage while preserving index settings.
        if requirements:
            subprocess.run(
                command
                + ["wheel", "--wheel-dir", str(destination)]
                + options
                + list(requirements),
                cwd=project_dir,
                check=True,
                env=resolution_env,
            )
        subprocess.run(
            command
            + ["download", "--only-binary=:all:", "--no-deps", "--dest", str(tools)]
            + options
            + ["pip"],
            cwd=project_dir,
            check=True,
        )
    wheels = list(tools.glob("*.whl"))
    if len(wheels) != 1 or parse_wheel_filename(wheels[0].name)[0] != "pip":
        raise BundleError("Expected exactly one pip tooling wheel")
    return wheels[0]


def _artifact(path: Path, root: Path, supported=None) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    relative_path = path.relative_to(root).as_posix()
    entry = dict(
        path=relative_path,
        size=path.stat().st_size,
        sha256=digest.hexdigest(),
    )
    role = artifact_role(relative_path)
    if role != "robot-cache":
        name, version, _, tags = parse_wheel_filename(path.name)
        if supported is not None and not tags.intersection(supported):
            raise BundleError(f"incompatible {role}: {path.name}")
        entry.update(name=str(name), version=str(version))
    return entry


def create_bundle(
    pyproject_path: Path,
    output: Path,
    *,
    find_links: Optional[Path] = None,
    use_certifi: bool = False,
) -> None:
    """Resolve online, validate, then publish an offline zipapp."""
    pyproject_path = pyproject_path.resolve()
    output = output.absolute()
    if os.path.lexists(output):
        raise FileExistsError(output)
    project = pyproject.loads(
        pyproject_path.read_text(encoding="utf-8"), base_path=pyproject_path.parent
    )
    requirements = project.get_install_reqs()
    packages = list(map(str, requirements))
    for requirement in requirements:
        if requirement.url is not None:
            _reject_credential_url(requirement.url)
    if find_links is not None:
        find_links = find_links.resolve()
    with tempfile.TemporaryDirectory(prefix="robotpy-bundle-") as temporary:
        root = Path(temporary)
        stage = root / "stage"
        stage.mkdir()
        robot = RobotpyInstaller(cache_root=stage / "robot-cache")
        robot.python_runtime = project.python_runtime
        robot.download_python(use_certifi)
        if packages:
            robot.pip_download(
                no_deps=False,
                pre=False,
                requirements=[],
                packages=packages,
                find_links=find_links,
                force_deps=True,
            )
            direct = [str(req) for req in requirements if req.url is not None]
            if direct:
                robot.pip_wheel(
                    no_deps=True,
                    pre=False,
                    requirements=[],
                    packages=direct,
                    find_links=find_links,
                )
        _stage_local_wheels(
            packages,
            stage / "local",
            project_dir=pyproject_path.parent,
            find_links=find_links,
            use_certifi=use_certifi,
        )
        platforms = (
            [installer._ROBOTPY_PYTHON_PLATFORM]
            + [
                f"manylinux_2_{n}_aarch64"
                for n in reversed(
                    range(
                        installer._ROBOTPY_MANYLINUX_MIN,
                        installer._ROBOTPY_MANYLINUX_MAX + 1,
                    )
                )
            ]
            + ["linux_aarch64"]
        )
        version = robot.python_runtime.version[:2]
        robot_tags = set(
            cpython_tags(
                version,
                abis=[f"cp{robot.python_runtime.version_num}"],
                platforms=platforms,
            )
        )
        robot_tags.update(
            compatible_tags(
                version,
                interpreter=f"cp{robot.python_runtime.version_num}",
                platforms=platforms,
            )
        )
        local_tags = set(sys_tags())
        artifacts = []
        for path in sorted(stage.rglob("*")):
            if not path.is_file():
                continue
            area = path.relative_to(stage).parts[0]
            supported = robot_tags if area == "robot-cache" else local_tags
            artifacts.append(_artifact(path, stage, supported))
        manifest = dict(
            format_version=_bundle_format.FORMAT_VERSION,
            creator_version=__version__,
            cache_year=int(installer._WPILIB_YEAR),
            artifacts=artifacts,
            local_python=dict(
                version=list(sys.version_info[:3]),
                implementation=sys.implementation.name,
                abi_flags=getattr(sys, "abiflags", ""),
            ),
            platform=dict(
                sys_platform=sys.platform,
                machine=platform.machine(),
                sysconfig_platform=sysconfig.get_platform(),
            ),
            robot_python=dict(
                version=list(robot.python_runtime.version),
                platform=installer._ROBOTPY_PYTHON_PLATFORM,
                runtime_path=robot._python_pkg_path.relative_to(stage).as_posix(),
            ),
        )
        shutil.copyfile(_LAUNCHER_SOURCE, stage / "__main__.py")
        shutil.copyfile(_bundle_format.__file__, stage / "_bundle_format.py")
        (stage / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        # Publish only the validated archive. The initial existence check is not
        # atomic with replacement, so a concurrent writer may be overwritten.
        fd, name = tempfile.mkstemp(prefix=f".{output.name}-", dir=output.parent)
        os.close(fd)
        candidate = Path(name)
        try:
            zipapp.create_archive(stage, candidate, compressed=True)
            validate_bundle(candidate)
            os.replace(candidate, output)
        finally:
            candidate.unlink(missing_ok=True)


def inspect_bundle(path: Path) -> str:
    """Return a validated inventory without importing any bundled code."""
    manifest = validate_bundle(path)
    local = manifest["local_python"]
    robot = manifest["robot_python"]
    platform = manifest["platform"]
    lines = [
        f"Offline bundle format {manifest['format_version']} (creator {manifest['creator_version']})",
        f"Local Python: {'.'.join(map(str, local['version']))} ({local['implementation']}, ABI flags: {local['abi_flags']!r})",
        f"Local platform: {platform['sys_platform']} / {platform['machine']} / {platform['sysconfig_platform']}",
        f"Robot Python: {'.'.join(map(str, robot['version']))}",
        f"Robot target: {robot['platform']}",
        f"Robot runtime: {robot['runtime_path']}",
        f"Cache year: {manifest['cache_year']}",
    ]
    for role, heading in (
        ("local-wheel", "Local wheels"),
        ("robot-wheel", "Robot wheels"),
        ("robot-cache", "Robot cache (non-wheel payloads)"),
        ("pip-tool", "Pip tooling"),
    ):
        lines.extend(("", heading + ":"))
        entries = sorted(
            (
                entry
                for entry in manifest["artifacts"]
                if artifact_role(entry["path"]) == role
            ),
            key=lambda entry: entry["path"],
        )
        for entry in entries:
            if role == "robot-cache":
                lines.append(f"  {entry['path']} ({entry['size']} bytes)")
            else:
                lines.append(f"  {entry['name']} {entry['version']} ({entry['path']})")
        if not entries:
            lines.append("  (none)")
    return "\n".join(lines)
