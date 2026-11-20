import argparse
import importlib
import sys
from pathlib import Path

import pytest

from robotpy.main import _run
from robotpy_installer.cli_installer import Installer
from test_offline_format import bundle_data, make_bundle


def adapters():
    # Assert availability first so missing implementation is a test failure.
    assert importlib.util.find_spec("robotpy_installer.cli_offline") is not None
    return importlib.import_module("robotpy_installer.cli_offline")


def run_cli(tmp_path, *args):
    return _run(
        ["--main", str(tmp_path / "robot.py"), "installer", "offline", *args],
        [("installer", Installer)],
    )


def test_offline_registered():
    assert "offline" in dict(Installer.subcommands)
    assert set(dict(adapters().InstallerOffline.subcommands)) == {
        "create-bundle",
        "inspect",
    }


@pytest.mark.parametrize("explicit", [False, True])
def test_create_parser_and_forwarding(tmp_path, monkeypatch, explicit):
    module = adapters()
    output = tmp_path / "out.pyz"
    links = tmp_path / "wheels"
    project = tmp_path / "other.toml" if explicit else tmp_path / "pyproject.toml"
    observed = []

    def create(pyproject_path, output_path, *, find_links, use_certifi):
        observed.append((pyproject_path, output_path, find_links, use_certifi))

    # Creation performs online downloads/builds; only replace that boundary.
    monkeypatch.setattr(module, "create_bundle", create)
    args = ["create-bundle", "--output", str(output)]
    if explicit:
        args += [
            "--pyproject",
            str(project),
            "--find-links",
            str(links),
            "--use-certifi",
        ]
    assert run_cli(tmp_path, *args) == 0
    assert observed == [(project, output, links if explicit else None, explicit)]
    assert not (tmp_path / "robot.py").exists()


def test_create_requires_output():
    parser = argparse.ArgumentParser()
    adapters().InstallerOfflineCreateBundle(parser)
    with pytest.raises(SystemExit) as exc:
        parser.parse_args([])
    assert exc.value.code == 2


def test_inspect_parser():
    parser = argparse.ArgumentParser()
    adapters().InstallerOfflineInspect(parser)
    assert parser.parse_args(["bundle.pyz"]).bundle == Path("bundle.pyz")


def test_inspect_other_platform_without_robot(tmp_path, bundle_data, capsys):
    adapters()
    manifest, files = bundle_data
    manifest["platform"]["sys_platform"] = (
        "win32" if sys.platform != "win32" else "linux"
    )
    archive = make_bundle(tmp_path, manifest, files)
    assert run_cli(tmp_path, "inspect", str(archive)) == 0
    report = capsys.readouterr().out
    for text in (
        "Local wheels",
        "local_demo 1.2",
        "Robot wheels",
        "robot_demo 2.3",
        "Pip tooling",
        manifest["platform"]["sys_platform"],
    ):
        assert text in report
    assert not (tmp_path / "robot.py").exists()


@pytest.mark.parametrize(
    "problem",
    [
        "missing-project",
        "malformed-project",
        "malformed-bundle",
        "bundle-error",
        "existing-output",
        "build-error",
    ],
)
def test_errors_are_user_facing(tmp_path, monkeypatch, capsys, problem):
    module = adapters()
    archive = tmp_path / "bundle.pyz"
    if problem == "malformed-bundle":
        archive.write_text("not a zip")
        args = ["inspect", str(archive)]
    else:
        args = ["create-bundle", "--output", str(archive)]
        if problem == "malformed-project":
            (tmp_path / "pyproject.toml").write_text("[tool.robotpy\n")
        elif problem == "existing-output":
            (tmp_path / "pyproject.toml").write_text(
                '[tool.robotpy]\nrobotpy_version = "2027.0.0a12"\n'
            )
            archive.write_text("preserve me")
        elif problem in ("bundle-error", "build-error"):
            import subprocess
            from robotpy_installer._bundle_format import BundleError

            def fail(*args, **kwargs):
                if problem == "build-error":
                    raise subprocess.CalledProcessError(1, ["pip", "wheel"])
                raise BundleError("incompatible robot wheel")

            monkeypatch.setattr(module, "create_bundle", fail)
    assert run_cli(tmp_path, *args) == 1
    error = capsys.readouterr().err
    assert "ERROR:" in error
    assert "Traceback" not in error
    if problem == "missing-project":
        assert "pyproject.toml" in error
    elif problem == "malformed-project":
        assert "pyproject.toml" in error
        assert "Invalid TOML" in error
        assert not archive.exists()
    elif problem == "bundle-error":
        assert "incompatible robot wheel" in error
    elif problem == "existing-output":
        assert "already exists" in error
        assert str(archive) in error
        assert archive.read_text() == "preserve me"
