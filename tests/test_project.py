import argparse
from unittest.mock import MagicMock

from robotpy_installer import cli_project


def test_update_robotpy_leaves_ignored_version_unchanged(tmp_path, monkeypatch):
    config = '[tool.robotpy]\nrobotpy_version = "ignored"\n'
    (tmp_path / "pyproject.toml").write_text(config)
    installer = MagicMock()
    monkeypatch.setattr(cli_project, "RobotpyInstaller", installer)

    result = cli_project.UpdateRobotpy(argparse.ArgumentParser()).run(tmp_path, False)

    assert result is False
    installer.assert_not_called()
    assert (tmp_path / "pyproject.toml").read_text() == config
