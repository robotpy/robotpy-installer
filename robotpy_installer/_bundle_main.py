"""Standalone offline zipapp launcher (standard library only).

Bundles are executable code and must come from a trusted publisher. Installation
and cache merging are not transactional; rerun after an interrupted operation.
"""

import argparse
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING, Optional, Sequence
import zipfile

if TYPE_CHECKING or __package__:
    from ._bundle_format import BundleError, artifact_role, validate_bundle
else:
    from _bundle_format import BundleError, artifact_role, validate_bundle


# -I excludes PYTHONPATH/user-site leakage. The validated pip wheel is the only
# tooling added, and is never installed into the destination just to run pip.
_TAG_CHECK = """
import sys
sys.path.insert(0, sys.argv.pop(1))
from pip._vendor.packaging.tags import sys_tags
from pip._vendor.packaging.utils import parse_wheel_filename
supported = set(sys_tags())
for path in sys.argv[1:]:
    from pathlib import Path
    if not supported.intersection(parse_wheel_filename(Path(path).name)[3]):
        sys.exit('Incompatible wheel: ' + Path(path).name)
"""
_PIP_BOOTSTRAP = """
import runpy, sys
sys.path.insert(0, sys.argv.pop(1))
runpy.run_module('pip', run_name='__main__')
"""


def _pip_environment():
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PIP_")}
    # pip treats devnull specially: disable *all* configuration files, including
    # global and virtualenv configuration. Do not use --break-system-packages.
    env["PIP_CONFIG_FILE"] = os.devnull
    return env


def check_compatibility(
    manifest: dict, wheel_paths: Sequence[Path], pip_wheel: Path
) -> None:
    """Check identity and actual wheel tags before any destination changes."""
    local = manifest["local_python"]
    target = manifest["platform"]
    checks = (
        (local["version"][:2], list(sys.version_info[:2]), "Python major/minor"),
        (local["implementation"], sys.implementation.name, "implementation"),
        (local["abi_flags"], getattr(sys, "abiflags", ""), "ABI flags"),
        (target["sys_platform"], sys.platform, "OS"),
        (target["machine"], platform.machine(), "architecture"),
        # sysconfig_platform is informational: deployment-target strings can
        # differ on compatible hosts. Wheel tags below enforce OS/ABI baselines.
    )
    for expected, actual, label in checks:
        if expected != actual:
            raise BundleError(
                f"Incompatible {label}: bundle requires {expected!r}, host is {actual!r}"
            )
    result = subprocess.run(
        [sys.executable, "-I", "-c", _TAG_CHECK, str(pip_wheel), str(pip_wheel)]
        + [str(p) for p in wheel_paths],
        env=_pip_environment(),
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise BundleError(
            "Incompatible wheel or unusable bundled pip tooling: "
            + (
                result.stderr.strip().splitlines()[-1]
                if result.stderr.strip()
                else "tag check failed"
            )
        )


def _safe_path(path: Path) -> None:
    """Refuse symlinks and Windows reparse points, including parent links."""
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        # st_file_attributes is available on Windows at our Python 3.10 floor;
        # Path.is_junction is not. Fail closed for all reparse-point types.
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise BundleError(f"Unsafe cache symlink/junction/reparse point: {part}")


def _cache_targets(manifest, cache_root):
    # Check the lexical path before normalization can erase a symlink/.. pair.
    _safe_path(Path(cache_root))
    root = Path(os.path.abspath(cache_root))
    _safe_path(root)
    targets = []
    for entry in manifest["artifacts"]:
        if artifact_role(entry["path"]) in ("robot-cache", "robot-wheel"):
            relative = Path(entry["path"]).relative_to("robot-cache")
            target = root / relative
            _safe_path(target)
            if target.exists() and not target.is_file():
                raise BundleError(f"Cache target is not a file: {target}")
            for parent in target.parents:
                if parent.exists() and not parent.is_dir():
                    raise BundleError(f"Cache parent is not a directory: {parent}")
            targets.append((entry["path"], target))
    return targets


def restore_cache(extracted_root: Path, manifest: dict, cache_root: Path) -> None:
    """Merge recorded artifacts only, preserving unrelated files.

    Existing symlinks are refused, and checked again immediately before writes.
    As with pip installation, the destination must not be concurrently modified
    by an untrusted process.
    """
    targets = _cache_targets(manifest, cache_root)
    for source, target in targets:
        _safe_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        _safe_path(target)
        fd, name = tempfile.mkstemp(prefix=".robotpy-", dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as destination:
                with (extracted_root / source).open("rb") as stream:
                    shutil.copyfileobj(stream, destination)
            _safe_path(target)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install a trusted offline RobotPy bundle"
    )
    parser.add_argument(
        "--cache-root", type=Path, help="Override the RobotPy cache directory"
    )
    args = parser.parse_args(argv)
    try:
        archive_path = Path(sys.argv[0]).absolute()
        manifest = validate_bundle(archive_path)
        cache_root = args.cache_root or (
            Path.home() / "wpilib" / str(manifest["cache_year"]) / "robotpy"
        )
        with tempfile.TemporaryDirectory(prefix="robotpy-offline-") as temporary:
            root = Path(temporary)
            with zipfile.ZipFile(archive_path) as archive:
                for entry in manifest["artifacts"]:
                    target = root / entry["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with (
                        archive.open(entry["path"]) as source,
                        target.open("wb") as dest,
                    ):
                        shutil.copyfileobj(source, dest)
            wheels = [
                root / e["path"]
                for e in manifest["artifacts"]
                if artifact_role(e["path"]) == "local-wheel"
            ]
            pip_wheel = next(
                root / e["path"]
                for e in manifest["artifacts"]
                if artifact_role(e["path"]) == "pip-tool"
            )
            check_compatibility(manifest, wheels, pip_wheel)
            _cache_targets(manifest, cache_root)
            if wheels:
                subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-c",
                        _PIP_BOOTSTRAP,
                        str(pip_wheel),
                        "--disable-pip-version-check",
                        "--no-cache-dir",
                        "--no-input",
                        "install",
                        "--no-index",
                        "--no-deps",
                    ]
                    + [str(p) for p in wheels],
                    env=_pip_environment(),
                    check=True,
                )
            restore_cache(root, manifest, cache_root)
        return 0
    except subprocess.CalledProcessError:
        print(
            "Offline bundle: installation failed. Use a writable virtualenv; cache was not merged. Fix the error and rerun.",
            file=sys.stderr,
        )
    except (BundleError, OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Offline bundle: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
