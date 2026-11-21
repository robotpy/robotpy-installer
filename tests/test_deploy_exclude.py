import argparse
import json
import pathlib

import pytest

from robotpy_installer import pyproject
from robotpy_installer.cli_deploy import Deploy, LocalDeploy


@pytest.mark.parametrize("deploy_cls", [Deploy, LocalDeploy])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    "patterns, excluded",
    [
        (["*.txt"], {"root.txt", "nested/root.txt"}),
        (["/root.txt"], {"root.txt"}),
        (["tests/"], {"tests/test_robot.py", "nested/tests/test_robot.py"}),
        (["data/**", "!data/required.json"], {"data/extra.json"}),
        (["data/** ", "!data/required.json"], {"data/extra.json"}),
        (["nested/**/"], {"nested/tests/test_robot.py", "nested/deep/test_a.py"}),
        (["data/", "!data/required.json"], {"data/extra.json", "data/required.json"}),
        (["nested/**/test_?.py"], {"nested/deep/test_a.py"}),
        (["# comment", "", "\\#notes"], {"#notes"}),
        ([], set()),
    ],
)
def test_deploy_exclusions(tmp_path, deploy_cls, dry_run, patterns, excluded):
    source = tmp_path / "source"
    source.mkdir()
    config = '[tool.robotpy]\nrobotpy_version = "ignored"\n'
    config += f"deploy_exclude = {json.dumps(patterns)}\n"
    (source / "pyproject.toml").write_text(config)
    files = {
        "robot.py",
        "root.txt",
        "nested/root.txt",
        "tests/test_robot.py",
        "nested/tests/test_robot.py",
        "data/extra.json",
        "data/required.json",
        "nested/deep/test_a.py",
        "#notes",
    }
    defaults = {"logs/log.txt", "capture.wpilog", ".hidden", "venv/lib.py"}
    for name in files | defaults:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    dest = tmp_path / "dest"
    deploy = deploy_cls(argparse.ArgumentParser())
    uploaded = deploy._copy_to_tmpdir(dest, source, dry_run=dry_run)
    expected = (files - excluded) | {"pyproject.toml"}
    assert {
        pathlib.Path(p).relative_to(source).as_posix() for p in uploaded
    } == expected
    if dry_run:
        assert not dest.exists()
    else:
        assert {
            p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()
        } == expected
        for name in files - excluded:
            assert (dest / name).read_text() == name


@pytest.mark.parametrize("value", ['"*.txt"', "42", '["*.txt", 1]', "{}"])
def test_invalid_deploy_exclude(value):
    with pytest.raises(pyproject.PyprojectError, match="deploy_exclude"):
        pyproject.loads(
            '[tool.robotpy]\nrobotpy_version="ignored"\ndeploy_exclude=' + value
        )


def test_no_pyproject(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "robot.py").write_text("pass")
    deploy = Deploy(argparse.ArgumentParser())
    assert deploy._copy_to_tmpdir(tmp_path / "dest", source) == [
        str(source / "robot.py")
    ]


@pytest.mark.parametrize("deploy_cls", [Deploy, LocalDeploy])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    "patterns, included",
    [
        ([], set()),
        (
            ["!/logs/", "!*.wpilog", "!/.hidden"],
            {"logs/log.txt", "logs/keep.txt", "capture.wpilog", ".hidden"},
        ),
        (["!logs/keep.txt"], set()),
        (["!/logs/", "logs/*", "!logs/keep.txt"], {"logs/keep.txt"}),
        (["!.config/", "!.config/keep.txt"], {".config/keep.txt"}),
        (["!*.pyc", "nested/*.pyc"], {"cache.pyc"}),
        (
            ["!venv/", "!ctre_sim/", "!__pycache__/"],
            {"venv/lib.py", "ctre_sim/state", "__pycache__/other"},
        ),
    ],
)
def test_override_default_exclusions(tmp_path, deploy_cls, dry_run, patterns, included):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        "[tool.robotpy]\ndeploy_exclude=" + json.dumps(patterns)
    )
    files = {
        "logs/log.txt",
        "logs/keep.txt",
        "capture.wpilog",
        ".hidden",
        ".config/keep.txt",
        "cache.pyc",
        "nested/cache.pyc",
        "venv/lib.py",
        "ctre_sim/state",
        "__pycache__/other",
        "package.whl",
        "package.ipk",
        "archive.zip",
        "archive.gz",
        "nested/.hidden",
        "nested/logs/log.txt",
    }
    for name in files:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    dest = tmp_path / "dest"
    uploaded = deploy_cls(argparse.ArgumentParser())._copy_to_tmpdir(
        dest, source, dry_run
    )
    expected = included | {"pyproject.toml"}
    assert {
        pathlib.Path(p).relative_to(source).as_posix() for p in uploaded
    } == expected
    if not dry_run:
        assert {
            p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()
        } == expected


def test_excluded_large_file_does_not_warn(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.robotpy]\ndeploy_exclude=["big.dat"]'
    )
    (tmp_path / "big.dat").write_bytes(b"x" * 250001)
    deploy = Deploy(argparse.ArgumentParser())
    assert deploy._check_large_files(tmp_path)
    assert "large files" not in capsys.readouterr().err
