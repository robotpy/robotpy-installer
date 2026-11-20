"""Shared, standard-library-only offline bundle validation.

This module is also copied into zipapps. Never import code from a bundle while
inspecting it. Integrity hashes detect corruption, not untrusted publishers.
"""

import hashlib
import json
import re
import stat
import zipfile
import zlib
from pathlib import Path

FORMAT_VERSION = 1
_CODE_FILES = {"__main__.py", "_bundle_format.py"}
_ROOTS = {"local", "tools", "robot-cache"}


class BundleError(ValueError):
    """An offline bundle is unsupported, malformed, or corrupt."""


def _require(condition, message):
    if not condition:
        raise BundleError(message)


def _text(value, field, *, empty=False):
    _require(
        isinstance(value, str) and (empty or bool(value)),
        f"Invalid {field}: expected a string",
    )


def _path(value, field):
    _text(value, field)
    _require(
        not any(c in value for c in "\\:\x00")
        and all(
            part not in ("", ".", "..")
            and not part.endswith((".", " "))
            and not re.fullmatch(
                r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]",
                part.split(".", 1)[0].rstrip(" ").upper(),
            )
            for part in value.split("/")
        ),
        f"Unsafe {field}: {value!r}",
    )


def artifact_role(path: str) -> str:
    """Classify an artifact by layout; callers validate path safety separately."""
    root, separator, relative = path.partition("/")
    _require(
        separator and relative and root in _ROOTS, f"Invalid artifact layout: {path}"
    )
    if root == "robot-cache":
        return "robot-wheel" if path.endswith(".whl") else "robot-cache"
    _require(path.endswith(".whl"), f"Invalid artifact layout: {path}")
    return "local-wheel" if root == "local" else "pip-tool"


def _portable_paths(paths):
    """Reject case-insensitive aliases, including implicit parent directories."""
    seen = {}
    for path in paths:
        parts = path.rstrip("/").split("/")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            key = prefix.casefold()
            _require(
                key not in seen or seen[key] == prefix,
                f"Portable path collision: {seen.get(key)!r} and {prefix!r}",
            )
            seen[key] = prefix


def _version(value, field):
    _require(
        isinstance(value, list)
        and len(value) == 3
        and all(type(n) is int and n >= 0 for n in value),
        f"Invalid {field}: expected [major, minor, patch]",
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _normalized_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _schema(manifest):
    _require(isinstance(manifest, dict), "manifest.json must contain an object")
    version = manifest.get("format_version")
    _require(
        type(version) is int and version == FORMAT_VERSION,
        f"Unsupported format_version: {version!r}",
    )
    _text(manifest.get("creator_version"), "creator_version")
    for section, fields in (
        ("local_python", ("implementation", "abi_flags")),
        ("platform", ("sys_platform", "machine", "sysconfig_platform")),
        ("robot_python", ("platform", "runtime_path")),
    ):
        value = manifest.get(section)
        _require(isinstance(value, dict), f"Invalid {section}: expected an object")
        for field in fields:
            _text(value.get(field), f"{section}.{field}", empty=field == "abi_flags")
        if section != "platform":
            _version(value.get("version"), f"{section}.version")
    year = manifest.get("cache_year")
    _require(
        type(year) is int and 1000 <= year <= 9999,
        "Invalid cache_year: expected a four-digit integer",
    )
    artifacts = manifest.get("artifacts")
    _require(isinstance(artifacts, list), "Invalid artifacts: expected a list")
    paths = {}
    distributions = set()
    tooling = []
    for entry in artifacts:
        _require(isinstance(entry, dict), "Invalid artifacts entry: expected an object")
        path = entry.get("path")
        _path(path, "artifact path")
        _require(path not in paths, f"Duplicate artifact path: {path}")
        role = artifact_role(path)
        size = entry.get("size")
        _require(type(size) is int and size >= 0, f"Invalid size for {path}")
        digest = entry.get("sha256")
        _require(
            isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest),
            f"Invalid sha256 for {path}",
        )
        if role != "robot-cache":
            for field in ("name", "version"):
                _text(entry.get(field), f"{path} {field}")
            name = _normalized_name(entry["name"])
            key = (role, name)
            _require(
                key not in distributions,
                f"Duplicate wheel distribution for {role}: {name}",
            )
            distributions.add(key)
            if role == "pip-tool":
                _require(name == "pip", f"pip-tool must be pip: {path}")
                tooling.append(entry)
        paths[path] = entry
    _portable_paths(paths)
    _require(len(tooling) == 1, "Exactly one pip-tool wheel is required")
    runtime = manifest["robot_python"]["runtime_path"]
    _path(runtime, "robot_python.runtime_path")
    _require(
        runtime in paths and artifact_role(runtime) == "robot-cache",
        f"Missing robot runtime cache artifact: {runtime}",
    )
    return paths


def validate_bundle(path: Path) -> dict:
    """Validate schema, members, and payload integrity without executing code.

    Host compatibility is deliberately not checked: inspection is portable.
    Payload hashes are streamed, including large robot runtime archives.
    Wheel contents are opaque; inventory is creator-recorded and pip validates
    wheels when installing them.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            members = {}
            for info in archive.infolist():
                name = info.orig_filename
                _path(name[:-1] if info.is_dir() else name, "archive member")
                _require(name not in members, f"Duplicate archive member: {name}")
                mode = stat.S_IFMT(info.external_attr >> 16)
                _require(
                    mode in (0, stat.S_IFREG, stat.S_IFDIR)
                    and not stat.S_ISLNK(info.external_attr >> 16),
                    f"Unsafe archive member type: {name}",
                )
                _require(
                    info.is_dir() == (mode == stat.S_IFDIR)
                    or mode == 0
                    or (mode == stat.S_IFREG and not info.is_dir()),
                    f"Invalid archive member type: {name}",
                )
                members[name] = info
            _portable_paths(members)
            for name in members:
                parts = name.rstrip("/").split("/")
                for index in range(1, len(parts)):
                    parent = "/".join(parts[:index])
                    _require(
                        parent not in members, f"File/directory conflict: {parent}"
                    )
                if name.endswith("/"):
                    _require(
                        name[:-1] not in members, f"File/directory conflict: {name}"
                    )
            for name in _CODE_FILES | {"manifest.json"}:
                _require(name in members, f"Missing required archive file: {name}")
            manifest = json.loads(
                archive.read("manifest.json"), object_pairs_hook=_unique_object
            )
            artifacts = _schema(manifest)
            allowed = set(artifacts) | _CODE_FILES | {"manifest.json"}
            for name, info in members.items():
                if info.is_dir():
                    _require(
                        name[:-1] in _ROOTS
                        or any(p.startswith(name) for p in artifacts),
                        f"Unexpected archive directory: {name}",
                    )
                else:
                    _require(name in allowed, f"Unexpected archive payload: {name}")
            for name, entry in artifacts.items():
                _require(name in members, f"Missing artifact: {name}")
                _require(
                    members[name].file_size == entry["size"],
                    f"Size mismatch for {name}",
                )
                digest = hashlib.sha256()
                size = 0
                with archive.open(name) as source:
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        digest.update(chunk)
                _require(size == entry["size"], f"Size mismatch for {name}")
                _require(
                    digest.hexdigest() == entry["sha256"],
                    f"SHA-256 mismatch for {name}",
                )
            return manifest
    except BundleError as exc:
        raise BundleError(f"{path}: {exc}") from exc
    except (
        OSError,
        ValueError,
        RuntimeError,
        NotImplementedError,
        zipfile.BadZipFile,
        zlib.error,
        EOFError,
    ) as exc:
        raise BundleError(f"{path}: cannot read bundle: {exc}") from exc
