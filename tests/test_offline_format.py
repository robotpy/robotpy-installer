import hashlib
import io
import json
import stat
import struct
import warnings
import zipfile

import pytest

from robotpy_installer import _bundle_format
from robotpy_installer._bundle_format import BundleError, validate_bundle
from robotpy_installer.offline import inspect_bundle


def wheel(name, version, tag="py3-none-any"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        prefix = f"{name}-{version}.dist-info"
        archive.writestr(
            f"{prefix}/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        )
        archive.writestr(f"{prefix}/WHEEL", f"Wheel-Version: 1.0\nTag: {tag}\n")
    return stream.getvalue()


@pytest.fixture
def bundle_data():
    files = {
        "__main__.py": b"raise AssertionError('must not execute launcher')",
        "_bundle_format.py": b"raise AssertionError('must not import bundle module')",
        "local/local_demo-1.2-py3-none-any.whl": wheel("local_demo", "1.2"),
        "robot-cache/robot_demo-2.3-py3-none-any.whl": wheel("robot_demo", "2.3"),
        "tools/pip-25.0-py3-none-any.whl": wheel("pip", "25.0"),
        "robot-cache/python-runtime.tar.gz": b"fixture runtime",
        "robot-cache/source.tar.gz": b"fixture source",
    }
    artifacts = []
    for path, data in list(files.items())[2:]:
        entry = dict(
            path=path,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        if path.endswith(".whl"):
            name, version = path.split("/")[-1].split("-")[:2]
            entry.update(name=name, version=version)
        artifacts.append(entry)
    manifest = dict(
        format_version=1,
        creator_version="2027.0.0",
        local_python=dict(version=[3, 14, 2], implementation="cpython", abi_flags=""),
        platform=dict(
            sys_platform="win32", machine="ARM64", sysconfig_platform="win-arm64"
        ),
        robot_python=dict(
            version=[3, 15, 1],
            platform="linux_armv7l",
            runtime_path="robot-cache/python-runtime.tar.gz",
        ),
        cache_year=2027,
        artifacts=artifacts,
    )
    return manifest, files


def make_bundle(tmp_path, manifest, files, *, raw=None, extra=(), symlink=None):
    path = tmp_path / "bundle.pyz"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "manifest.json", json.dumps(manifest) if raw is None else raw
            )
            for name, data in list(files.items()) + list(extra):
                info = zipfile.ZipInfo(name)
                if name == symlink:
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, data)
    return path


def test_inspect_lists_both_inventories(tmp_path, bundle_data):
    manifest, files = bundle_data
    path = make_bundle(tmp_path, manifest, files)
    assert validate_bundle(path) == manifest
    report = inspect_bundle(path)
    for text in (
        "Local wheels",
        "Robot wheels",
        "local_demo 1.2",
        "robot_demo 2.3",
        "3.14.2",
        "3.15.1",
        "win32",
        "ARM64",
        "win-arm64",
        "linux_armv7l",
        "Pip tooling",
        "pip 25.0",
        "Robot cache",
        "source.tar.gz",
    ):
        assert text in report
    assert report.count("pip 25.0") == 1


@pytest.mark.parametrize(
    "path,expected",
    [
        ("local/demo-1-py3-none-any.whl", "local-wheel"),
        ("tools/pip-25-py3-none-any.whl", "pip-tool"),
        ("robot-cache/pip_cache/demo-1-py3-none-any.whl", "robot-wheel"),
        ("robot-cache/pkg_cache/python.tar.gz", "robot-cache"),
    ],
)
def test_artifact_role_from_layout(path, expected):
    assert _bundle_format.artifact_role(path) == expected


@pytest.mark.parametrize(
    "path", ["other/demo.whl", "local/source.tar.gz", "tools/pip.tar.gz", "demo.whl"]
)
def test_invalid_artifact_layout(tmp_path, bundle_data, path):
    manifest, files = bundle_data
    entry = manifest["artifacts"][0]
    files[path] = files.pop(entry["path"])
    entry["path"] = path
    with pytest.raises(BundleError, match="layout"):
        validate_bundle(make_bundle(tmp_path, manifest, files))


def test_validation_treats_wheel_payload_as_opaque(tmp_path, bundle_data):
    manifest, files = bundle_data
    entry = manifest["artifacts"][0]
    payload = b"opaque wheel payload: pip validates this at installation"
    files[entry["path"]] = payload
    entry.update(size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    path = make_bundle(tmp_path, manifest, files)
    assert validate_bundle(path) == manifest
    assert "local_demo 1.2" in inspect_bundle(path)


def test_inspect_never_executes_main(tmp_path, bundle_data):
    manifest, files = bundle_data
    assert "Local wheels" in inspect_bundle(make_bundle(tmp_path, manifest, files))


@pytest.mark.parametrize(
    "field,value",
    [
        ("format_version", 999),
        ("format_version", True),
        ("creator_version", 7),
        ("local_python", []),
        ("platform", None),
        ("robot_python", "3.15"),
        ("cache_year", "2027"),
        ("cache_year", True),
        ("artifacts", {}),
    ],
)
def test_invalid_manifest_fields(tmp_path, bundle_data, field, value):
    manifest, files = bundle_data
    manifest[field] = value
    with pytest.raises(BundleError, match=field):
        validate_bundle(make_bundle(tmp_path, manifest, files))


@pytest.mark.parametrize(
    "field",
    [
        "format_version",
        "creator_version",
        "local_python",
        "platform",
        "robot_python",
        "cache_year",
        "artifacts",
    ],
)
def test_missing_fields(tmp_path, bundle_data, field):
    manifest, files = bundle_data
    del manifest[field]
    with pytest.raises(BundleError, match=field):
        validate_bundle(make_bundle(tmp_path, manifest, files))


@pytest.mark.parametrize(
    "raw", ["{", "[]", "null", '{"format_version":1,"format_version":1}']
)
def test_invalid_json(tmp_path, bundle_data, raw):
    with pytest.raises(BundleError):
        validate_bundle(make_bundle(tmp_path, *bundle_data, raw=raw))


@pytest.mark.parametrize(
    "case",
    [
        "missing-payload",
        "missing-main",
        "missing-module",
        "duplicate-member",
        "duplicate-artifact",
        "hash",
        "size",
        "negative-size",
        "bool-size",
        "bad-hash",
        "unexpected",
        "symlink",
        "missing-runtime",
        "wheel-runtime",
        "no-pip",
        "wrong-pip",
        "duplicate-distribution",
        "name",
        "version",
        "local-version",
        "implementation",
        "abi",
        "machine",
        "robot-version",
        "robot-platform",
    ],
)
def test_reject_malformed_bundle(tmp_path, bundle_data, case):
    manifest, files = bundle_data
    artifacts = manifest["artifacts"]
    first = artifacts[0]
    extra = ()
    symlink = None
    if case == "missing-payload":
        del files[first["path"]]
    elif case in ("missing-main", "missing-module"):
        del files["__main__.py" if case == "missing-main" else "_bundle_format.py"]
    elif case == "duplicate-member":
        extra = [(first["path"], files[first["path"]])]
    elif case == "duplicate-artifact":
        artifacts.append(dict(first))
    elif case in ("hash", "bad-hash"):
        first["sha256"] = "0" * 64 if case == "hash" else "xyz"
    elif case in ("size", "negative-size", "bool-size"):
        first["size"] = {"size": 1, "negative-size": -1, "bool-size": True}[case]
    elif case == "unexpected":
        files["local/unlisted.txt"] = b"extra"
    elif case == "symlink":
        symlink = first["path"]
    elif case == "missing-runtime":
        manifest["robot_python"]["runtime_path"] = "robot-cache/absent.tar.gz"
    elif case == "wheel-runtime":
        manifest["robot_python"]["runtime_path"] = artifacts[1]["path"]
    elif case == "no-pip":
        entry = artifacts.pop(2)
        del files[entry["path"]]
    elif case == "wrong-pip":
        artifacts[2]["name"] = "not-pip"
    elif case == "duplicate-distribution":
        entry = dict(first, path="local/nested/local_demo-1.2-py3-none-any.whl")
        artifacts.append(entry)
        files[entry["path"]] = files[first["path"]]
    elif case in ("name", "version"):
        first[case] = None
    else:
        section, field, value = {
            "local-version": ("local_python", "version", [3, True, 1]),
            "implementation": ("local_python", "implementation", 5),
            "abi": ("local_python", "abi_flags", None),
            "machine": ("platform", "machine", ""),
            "robot-version": ("robot_python", "version", [3, 15]),
            "robot-platform": ("robot_python", "platform", []),
        }[case]
        manifest[section][field] = value
    with pytest.raises(BundleError):
        validate_bundle(
            make_bundle(tmp_path, manifest, files, extra=extra, symlink=symlink)
        )


@pytest.mark.parametrize(
    "unsafe",
    [
        "/absolute",
        "../escape",
        "local/../escape",
        "local\\escape",
        "C:/escape",
        "local//escape",
        "local/./escape",
    ],
)
@pytest.mark.parametrize("in_manifest", [False, True])
def test_reject_unsafe_paths(tmp_path, bundle_data, unsafe, in_manifest):
    manifest, files = bundle_data
    if in_manifest:
        manifest["artifacts"][0]["path"] = unsafe
    else:
        files[unsafe] = b"unsafe"
    with pytest.raises(BundleError):
        validate_bundle(make_bundle(tmp_path, manifest, files))


@pytest.mark.parametrize(
    "unsafe",
    [
        "CON",
        "nul.txt",
        "Aux",
        "PRN.tar",
        "COM1",
        "lpt9.log",
        "COM¹",
        "LPT²",
        "source.",
        "source ",
        "nested./source",
        "nested /source",
    ],
)
@pytest.mark.parametrize("in_manifest", [False, True])
def test_windows_unsafe_paths(tmp_path, bundle_data, unsafe, in_manifest):
    manifest, files = bundle_data
    entry = manifest["artifacts"][-1]
    old = entry["path"]
    new = "robot-cache/" + unsafe
    entry["path"] = new
    if not in_manifest:
        files[new] = files.pop(old)
    with pytest.raises(BundleError, match="Unsafe"):
        validate_bundle(make_bundle(tmp_path, manifest, files))


@pytest.mark.parametrize(
    "names",
    [
        ("other.tar.gz", "OTHER.tar.gz"),
        ("nested/first", "NESTED/second"),
        ("straße", "STRASSE"),
        ("Parent", "parent/child"),
    ],
)
@pytest.mark.parametrize("in_manifest", [False, True])
def test_casefold_path_collisions(tmp_path, bundle_data, names, in_manifest):
    manifest, files = bundle_data
    for name in names:
        path = "robot-cache/" + name
        data = b"collision"
        manifest["artifacts"].append(
            dict(
                path=path,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
        if not in_manifest:
            files[path] = data
    with pytest.raises(BundleError, match="Portable path collision"):
        validate_bundle(make_bundle(tmp_path, manifest, files))


def test_nul_original_archive_name(tmp_path, bundle_data):
    manifest, files = bundle_data
    files["__main__.py!hidden"] = files.pop("__main__.py")
    path = make_bundle(tmp_path, manifest, files)
    data = path.read_bytes()
    assert data.count(b"__main__.py!hidden") == 2
    path.write_bytes(data.replace(b"__main__.py!hidden", b"__main__.py\x00hidden"))
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("__main__.py")
        assert info.orig_filename == "__main__.py\x00hidden"
    with pytest.raises(BundleError, match="Unsafe archive member"):
        validate_bundle(path)


def test_nul_manifest_path(tmp_path, bundle_data):
    manifest, files = bundle_data
    manifest["artifacts"][0]["path"] += "\x00hidden"
    with pytest.raises(BundleError, match="Unsafe artifact path"):
        validate_bundle(make_bundle(tmp_path, manifest, files))


def test_directory_entries(tmp_path, bundle_data):
    manifest, files = bundle_data
    files.update({"local/": b"", "tools/": b"", "robot-cache/": b""})
    assert validate_bundle(make_bundle(tmp_path, manifest, files)) == manifest


def test_not_a_zip(tmp_path):
    path = tmp_path / "bad.pyz"
    path.write_bytes(b"not a zip")
    with pytest.raises(BundleError, match="bad.pyz"):
        validate_bundle(path)


def test_conflicting_file_and_directory(tmp_path, bundle_data):
    manifest, files = bundle_data
    data = b"nested"
    path = "robot-cache/python-runtime.tar.gz/child"
    files[path] = data
    manifest["artifacts"].append(
        dict(
            path=path,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
    )
    with pytest.raises(BundleError, match="conflict"):
        validate_bundle(make_bundle(tmp_path, manifest, files))


def test_corrupt_compressed_payload(tmp_path, bundle_data):
    manifest, files = bundle_data
    path = tmp_path / "compressed.pyz"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in files.items():
            archive.writestr(name, data)
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("robot-cache/python-runtime.tar.gz")
    data = bytearray(path.read_bytes())
    name_len, extra_len = struct.unpack_from("<HH", data, info.header_offset + 26)
    start = info.header_offset + 30 + name_len + extra_len
    data[start] = 0xFF  # Reserved DEFLATE block type, not a valid stream.
    path.write_bytes(data)
    with pytest.raises(BundleError, match="compressed.pyz"):
        validate_bundle(path)


def test_runtime_hashing_is_streamed(tmp_path, bundle_data, monkeypatch):
    manifest, files = bundle_data
    runtime = "robot-cache/python-runtime.tar.gz"
    files[runtime] = b"r" * (3 * 1024 * 1024)
    entry = next(e for e in manifest["artifacts"] if e["path"] == runtime)
    entry.update(
        size=len(files[runtime]), sha256=hashlib.sha256(files[runtime]).hexdigest()
    )
    path = make_bundle(tmp_path, manifest, files)
    original_read = zipfile.ZipExtFile.read

    def bounded_read(self, n=-1):
        if self.name == runtime:
            assert 0 < n <= 1024 * 1024
        return original_read(self, n)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", bounded_read)
    assert validate_bundle(path) == manifest


def test_empty_project_inventories(tmp_path, bundle_data):
    manifest, files = bundle_data
    for entry in manifest["artifacts"][:2]:
        del files[entry["path"]]
    del manifest["artifacts"][:2]
    report = inspect_bundle(make_bundle(tmp_path, manifest, files))
    assert "Local wheels:\n  (none)" in report
    assert "Robot wheels:\n  (none)" in report
    assert "pip 25.0" in report


def test_missing_archive(tmp_path):
    with pytest.raises(BundleError, match="absent.pyz"):
        validate_bundle(tmp_path / "absent.pyz")
