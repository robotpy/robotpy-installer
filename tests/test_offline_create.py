import os
import io
import json
import tarfile
from pathlib import Path
import importlib.metadata
import subprocess
import sys
import zipfile

import pytest

from robotpy_installer import offline
from robotpy_installer._bundle_format import BundleError, validate_bundle
from robotpy_installer.installer import RobotpyInstaller
from test_offline_format import wheel

_REAL_STAGE = offline._stage_local_wheels


@pytest.fixture
def creation(tmp_path, monkeypatch):
    project = tmp_path / "custom.toml"
    project.write_text(
        '[tool.robotpy]\nrobotpy_version = "ignored"\npython_version = "3.14"\nrequires = ["demo==1.0"]\n'
    )
    calls = []

    def runtime(self, use_certifi):
        calls.append(
            ("runtime", self.python_runtime.version, use_certifi, self.cache_root)
        )
        self.pkg_cache.mkdir(parents=True)
        self._python_pkg_path.write_bytes(b"runtime")

    def download(self, **kwargs):
        calls.append(("download", kwargs))
        self.pip_cache.mkdir(parents=True, exist_ok=True)
        (self.pip_cache / "demo-1.0-py3-none-any.whl").write_bytes(wheel("demo", "1.0"))

    def build(self, **kwargs):
        calls.append(("build", kwargs))

    def local(requirements, destination, **kwargs):
        calls.append(("local", requirements, kwargs))
        destination.mkdir(parents=True)
        if requirements:
            (destination / "demo-1.0-py3-none-any.whl").write_bytes(
                wheel("demo", "1.0")
            )
        tools = destination.parent / "tools"
        tools.mkdir()
        pip = tools / "pip-25.0-py3-none-any.whl"
        pip.write_bytes(wheel("pip", "25.0"))
        return pip

    monkeypatch.setattr(RobotpyInstaller, "download_python", runtime)
    monkeypatch.setattr(RobotpyInstaller, "pip_download", download)
    monkeypatch.setattr(RobotpyInstaller, "pip_wheel", build)
    monkeypatch.setattr(offline, "_stage_local_wheels", local, raising=False)
    return project, tmp_path / "bundle.pyz", calls


def test_create_uses_project_requirements_and_runtime(creation):
    project, output, calls = creation
    offline.create_bundle(project, output, find_links=project.parent, use_certifi=True)
    manifest = validate_bundle(output)
    assert "requirements" not in manifest
    assert all("role" not in a and "tags" not in a for a in manifest["artifacts"])
    assert manifest["robot_python"]["version"][:2] == [3, 14]
    assert manifest["cache_year"] == 2027
    assert calls[0][2] is True
    download = next(c[1] for c in calls if c[0] == "download")
    assert download == dict(
        no_deps=False,
        pre=False,
        requirements=[],
        packages=["demo==1.0"],
        find_links=project.parent,
    )
    local = next(c for c in calls if c[0] == "local")
    assert local[2] == dict(
        project_dir=project.parent, find_links=project.parent, use_certifi=True
    )
    assert {
        (a["path"].split("/")[0], a.get("name")) for a in manifest["artifacts"]
    } >= {
        ("local", "demo"),
        ("robot-cache", "demo"),
        ("tools", "pip"),
    }


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.org/demo.tar.gz",
        "https://user:sec'ret@example.org/demo.tar.gz",
        "https://secret@example.org/demo.tar.gz",
    ],
)
def test_rejects_credential_requirements_before_resolution(creation, url):
    project, output, calls = creation
    project.write_text(
        '[tool.robotpy]\nrobotpy_version="ignored"\nrequires='
        + json.dumps(["demo @ " + url])
    )
    with pytest.raises(BundleError, match="credential") as exc:
        offline.create_bundle(project, output)
    assert "secret" not in str(exc.value)
    assert calls == []
    assert not output.exists()


@pytest.mark.parametrize("kind", ["METADATA", "direct_url.json", "source"])
@pytest.mark.parametrize("password", ["secret", "sec'ret", "sec'\"ret"])
def test_package_contents_are_redistributed_unchanged(
    creation, monkeypatch, kind, password
):
    project, output, _ = creation
    original = RobotpyInstaller.pip_download
    supplied = {}

    def download(self, **kwargs):
        original(self, **kwargs)
        secret = f"https://user:{password}@example.org/demo.tar.gz".encode()
        if kind == "source":
            with tarfile.open(self.pip_cache / "demo.tar.gz", "w:gz") as archive:
                member = tarfile.TarInfo("demo/PKG-INFO")
                member.size = len(secret)
                archive.addfile(member, io.BytesIO(secret))
        else:
            path = self.pip_cache / "demo-1.0-py3-none-any.whl"
            with zipfile.ZipFile(path) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            members["demo-1.0.dist-info/" + kind] = (
                b"Metadata-Version: 2.1\nName: demo\nVersion: 1.0\nRequires-Dist: child @ "
                + secret
                + b"\n"
                if kind == "METADATA"
                else json.dumps({"url": secret.decode()}).encode()
            )
            with zipfile.ZipFile(path, "w") as archive:
                for name, data in members.items():
                    archive.writestr(name, data)
        supplied.update({f.name: f.read_bytes() for f in self.pip_cache.iterdir()})

    monkeypatch.setattr(RobotpyInstaller, "pip_download", download)
    offline.create_bundle(project, output)
    with zipfile.ZipFile(output) as archive:
        payloads = {
            Path(n).name: archive.read(n)
            for n in archive.namelist()
            if n.startswith("robot-cache/pip_cache/") and not n.endswith("/")
        }
    assert payloads == supplied
    assert "Robot wheels" in offline.inspect_bundle(output)


@pytest.mark.parametrize(
    "url",
    [
        "git+https://github.com/example/demo.git#subdirectory=python",
        "https://example.org/demo.tar.gz?version=1#subdirectory=python&sha256=abcd",
    ],
)
def test_direct_url_components_are_not_filtered(url):
    offline._reject_credential_url(url)


@pytest.mark.parametrize(
    "suffix",
    [
        "?opaque=secret",
        "#opaque=secret",
        "?version=1&token=secret",
        "#subdirectory=python&auth=secret",
    ],
)
def test_query_and_fragment_parameters_are_not_filtered(creation, suffix):
    project, output, _ = creation
    url = "https://example.org/demo.tar.gz" + suffix
    project.write_text(
        '[tool.robotpy]\nrobotpy_version="ignored"\nrequires='
        + json.dumps(["demo @ " + url])
    )
    offline.create_bundle(project, output)
    with zipfile.ZipFile(output) as archive:
        assert url not in archive.read("manifest.json").decode()
    assert "requirements" not in validate_bundle(output)


def test_create_preserves_project_bytes(creation):
    project, output, _ = creation
    before = project.read_bytes()
    offline.create_bundle(project, output)
    assert project.read_bytes() == before


def test_empty_requirements_still_bundles_runtime_and_pip(creation):
    project, output, calls = creation
    project.write_text('[tool.robotpy]\nrobotpy_version="ignored"\n')
    offline.create_bundle(project, output)
    manifest = validate_bundle(output)
    assert "requirements" not in manifest
    assert {a["path"].split("/")[0] for a in manifest["artifacts"]} == {
        "robot-cache",
        "tools",
    }
    assert not any(c[0] == "download" for c in calls)


def test_staging_excludes_existing_cache(creation, monkeypatch, tmp_path):
    project, output, calls = creation
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / "wpilib/2027/robotpy/pip_cache"
    cache.mkdir(parents=True)
    (cache / "unrelated.whl").write_bytes(b"old")
    offline.create_bundle(project, output)
    assert calls[0][3] != cache.parent
    with zipfile.ZipFile(output) as archive:
        assert not any("unrelated" in n for n in archive.namelist())


@pytest.mark.parametrize("tag", ["py3-none-any", "cp314-cp314-win_amd64"])
def test_direct_urls_build_wheels_and_validate_robot_tags(creation, monkeypatch, tag):
    project, output, calls = creation
    project.write_text(
        '[tool.robotpy]\nrobotpy_version="ignored"\nrequires=["demo @ file:./demo.tar.gz"]\n'
    )

    def build(self, **kwargs):
        calls.append(("build", kwargs))
        for p in self.pip_cache.glob("*.whl"):
            p.unlink()
        (self.pip_cache / f"demo-1.0-{tag}.whl").write_bytes(wheel("demo", "1.0", tag))

    monkeypatch.setattr(RobotpyInstaller, "pip_wheel", build)
    if "win" in tag:
        with pytest.raises(BundleError, match="incompatible"):
            offline.create_bundle(project, output)
        assert not output.exists()
    else:
        offline.create_bundle(project, output)
        args = next(c[1] for c in calls if c[0] == "build")
        assert args["no_deps"] is True and args["pre"] is False
        assert project.parent.as_uri() in args["packages"][0]


def test_relative_urls_use_project_directory(creation):
    project, output, calls = creation
    project.write_text(
        '[tool.robotpy]\nrobotpy_version="ignored"\nrequires=["demo @ file:./demo.tar.gz"]\n'
    )
    offline.create_bundle(project, output)
    local = next(c for c in calls if c[0] == "local")
    assert project.parent.as_uri() in local[1][0]
    assert local[2]["project_dir"] == project.parent


def test_failed_creation_has_no_output(creation, monkeypatch):
    project, output, _ = creation

    def fail(*args, **kwargs):
        raise RuntimeError("resolution failed")

    monkeypatch.setattr(offline, "_stage_local_wheels", fail)
    with pytest.raises(RuntimeError):
        offline.create_bundle(project, output)
    assert not output.exists()
    assert not list(output.parent.glob(".bundle*"))


def test_existing_output_is_preserved(creation):
    project, output, calls = creation
    output.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        offline.create_bundle(project, output)
    assert output.read_bytes() == b"existing"
    assert calls == []


def test_publication_uses_replace_after_validation(creation, monkeypatch):
    project, output, _ = creation
    validate = offline.validate_bundle
    replace = os.replace
    validated = []
    published = []

    def checked_validate(path):
        result = validate(path)
        validated.append(path)
        return result

    def checked_replace(source, destination):
        assert validated == [source]
        assert source.parent == output.parent
        assert destination == output
        replace(source, destination)
        published.append(destination)

    monkeypatch.setattr(os, "replace", checked_replace)
    monkeypatch.setattr(offline, "validate_bundle", checked_validate)
    offline.create_bundle(project, output)
    assert published == [output]
    assert validate_bundle(output)["artifacts"]
    assert not list(output.parent.glob(".bundle*"))


@pytest.mark.parametrize("failure", ["validation", "publication"])
def test_finalization_failure_cleans_up(creation, monkeypatch, failure):
    project, output, _ = creation

    def fail(*args, **kwargs):
        raise RuntimeError("finalization failed")

    if failure == "validation":
        monkeypatch.setattr(offline, "validate_bundle", fail)
    else:
        monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(RuntimeError, match="finalization failed"):
        offline.create_bundle(project, output)
    assert not output.exists()
    assert not list(output.parent.glob(".bundle*"))


def test_publication_race_may_overwrite_other_file(creation, monkeypatch):
    project, output, _ = creation
    validate = offline.validate_bundle

    def race(path):
        result = validate(path)
        output.write_bytes(b"other writer")
        return result

    monkeypatch.setattr(offline, "validate_bundle", race)
    offline.create_bundle(project, output)
    assert validate_bundle(output)["artifacts"]
    assert not list(output.parent.glob(".bundle*"))


def test_local_resolution_uses_clean_venv(tmp_path, monkeypatch):
    calls = []
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules, "certifi", SimpleNamespace(where=lambda: "/fixture/ca.pem")
    )

    class Builder:
        def __init__(self, **kwargs):
            assert kwargs == dict(with_pip=True, system_site_packages=False)

        def create(self, path):
            calls.append(("venv", Path(path)))

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if "download" in args:
            dest = Path(args[args.index("--dest") + 1])
            (dest / "pip-25.0-py3-none-any.whl").write_bytes(wheel("pip", "25.0"))
        else:
            dest = Path(args[args.index("--wheel-dir") + 1])
            (dest / "demo-1.0-py3-none-any.whl").write_bytes(wheel("demo", "1.0"))

    monkeypatch.setattr("venv.EnvBuilder", Builder)
    monkeypatch.setattr("subprocess.run", run)
    pip = offline._stage_local_wheels(
        ["demo"],
        tmp_path / "local",
        project_dir=tmp_path,
        find_links=tmp_path,
        use_certifi=True,
    )
    assert pip.name.startswith("pip-")
    for args, kwargs in calls[1:]:
        assert str(calls[0][1]) in args[0] and args[0] != sys.executable
        assert "--find-links" in args and "--cert" in args
        assert kwargs["cwd"] == tmp_path and kwargs["check"] is True
    build = calls[1][0]
    assert "wheel" in build and "--no-deps" not in build
    assert "--no-deps" in calls[2][0]


@pytest.mark.parametrize(
    "settings",
    [
        "clean",
        "env",
        "global",
        "wheel",
        "alias-env",
        "alias-config",
        "build-env",
        "build-config",
    ],
)
def test_real_resolution_includes_creator_visible_distribution(
    creation, tmp_path, monkeypatch, settings
):
    project, output, _ = creation
    monkeypatch.setattr(offline, "_stage_local_wheels", _REAL_STAGE)
    links = tmp_path / "links"
    links.mkdir()

    # demo -> built (sdist) -> leaf proves both closure and creation-time build.
    def dependent_wheel(name, dependency):
        with zipfile.ZipFile(io.BytesIO(wheel(name, "1.0"))) as original:
            result = io.BytesIO()
            with zipfile.ZipFile(result, "w") as archive:
                for member in original.namelist():
                    data = original.read(member)
                    if member.endswith("/METADATA"):
                        data += f"Requires-Dist: {dependency}==1.0\n".encode()
                    archive.writestr(member, data)
            return result.getvalue()

    (links / "demo-1.0-py3-none-any.whl").write_bytes(dependent_wheel("demo", "built"))
    (links / "leaf-1.0-py3-none-any.whl").write_bytes(wheel("leaf", "1.0"))
    from test_offline_run import make_wheel

    make_wheel(links / "buildhelper-1.0-py3-none-any.whl", "buildhelper", "1.0")
    source = tmp_path / "built-1.0"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[build-system]\nrequires=["buildhelper==1.0"]\nbuild-backend="backend"\nbackend-path=["."]\n'
    )
    (source / "backend.py").write_text(
        "from pathlib import Path\nimport buildhelper\n"
        "def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):\n"
        "    name = 'built-1.0-py3-none-any.whl'\n"
        f"    Path(wheel_directory, name).write_bytes({dependent_wheel('built', 'leaf')!r})\n"
        "    return name\n"
    )
    with tarfile.open(links / "built-1.0.tar.gz", "w:gz") as archive:
        archive.add(source, arcname="built-1.0")
    (links / "pip-25.0-py3-none-any.whl").write_bytes(wheel("pip", "25.0"))
    installed = tmp_path / "creator"
    metadata = installed / "demo-1.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n"
    )
    monkeypatch.syspath_prepend(str(installed))
    monkeypatch.setenv("PYTHONPATH", str(installed))
    assert importlib.metadata.version("demo") == "1.0"
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_CONFIG_FILE", os.devnull)
    # Acquisition settings must survive while closure/build settings are overridden.
    monkeypatch.setenv("PIP_FIND_LINKS", str(links))
    if settings == "env":
        monkeypatch.setenv("PIP_NO_DEPS", "1")
    elif settings == "alias-env":
        monkeypatch.setenv("PIP_NO_DEPENDENCIES", "1")
    elif settings == "alias-config":
        config = tmp_path / "pip.conf"
        config.write_text("[wheel]\nno-dependencies = true\n")
        monkeypatch.setenv("PIP_CONFIG_FILE", str(config))
    elif settings in {"global", "wheel"}:
        config = tmp_path / "pip.conf"
        config.write_text(f"[{settings}]\nno-deps = true\n")
        monkeypatch.setenv("PIP_CONFIG_FILE", str(config))
    elif settings == "build-env":
        # pip maps this negative-named configuration directly to build_isolation.
        monkeypatch.setenv("PIP_NO_BUILD_ISOLATION", "0")
        monkeypatch.setenv("PIP_USE_PEP517", "0")
    elif settings == "build-config":
        config = tmp_path / "pip.conf"
        config.write_text("[wheel]\nno-build-isolation = false\nuse-pep517 = false\n")
        monkeypatch.setenv("PIP_CONFIG_FILE", str(config))
    run = subprocess.run
    checked = []

    def checked_run(args, **kwargs):
        if "pip" in args:
            probe = run(
                [
                    args[0],
                    "-I",
                    "-c",
                    'import importlib.metadata; assert not list(importlib.metadata.distributions(name="demo"))',
                ],
                check=True,
            )
            checked.append(probe.returncode)
        return run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", checked_run)
    offline.create_bundle(project, output, find_links=links)
    assert checked == [0, 0]
    manifest = validate_bundle(output)
    assert {
        a["name"] for a in manifest["artifacts"] if a["path"].startswith("local/")
    } == {
        "demo",
        "built",
        "leaf",
    }
    assert not list(links.glob("built-*.whl"))
    with zipfile.ZipFile(output) as archive:
        with zipfile.ZipFile(
            io.BytesIO(archive.read("local/built-1.0-py3-none-any.whl"))
        ) as built:
            assert b"Requires-Dist: leaf==1.0" in built.read(
                "built-1.0.dist-info/METADATA"
            )


def test_real_source_url_with_version_and_subdirectory(tmp_path, monkeypatch):
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    links = tmp_path / "links"
    links.mkdir()
    source = tmp_path / "python"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n'
    )
    (source / "backend.py").write_text(
        "from pathlib import Path\n"
        "def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):\n"
        "    name = 'demo-1.0-py3-none-any.whl'\n"
        f"    Path(wheel_directory, name).write_bytes({wheel('demo', '1.0')!r})\n"
        "    return name\n"
    )
    with tarfile.open(links / "demo.tar.gz", "w:gz") as archive:
        archive.add(source, arcname="demo/python")
    (links / "pip-25.0-py3-none-any.whl").write_bytes(wheel("pip", "25.0"))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_CONFIG_FILE", os.devnull)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(links))
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/demo.tar.gz?version=1#subdirectory=python"
        offline._reject_credential_url(url)
        _REAL_STAGE(
            ["demo @ " + url],
            tmp_path / "local",
            project_dir=tmp_path,
            find_links=links,
            use_certifi=False,
        )
        result = tmp_path / "local/demo-1.0-py3-none-any.whl"
        assert result.is_file()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
