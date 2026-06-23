import pathlib

from robotpy_installer.installer import RobotpyInstaller, _WPILIB_YEAR


def test_installer_default_cache_root_is_wpilib_home():
    installer = RobotpyInstaller(log_startup=False)

    assert (
        installer.cache_root
        == pathlib.Path.home() / "wpilib" / _WPILIB_YEAR / "robotpy"
    )
    assert installer.pip_cache == installer.cache_root / "pip_cache"
    assert installer.pkg_cache == installer.cache_root / "pkg_cache"


def test_installer_cache_root_can_be_overridden(tmp_path):
    cache_root = tmp_path / "cache"

    installer = RobotpyInstaller(log_startup=False, cache_root=cache_root)

    assert installer.cache_root == cache_root
    assert installer.pip_cache == cache_root / "pip_cache"
    assert installer.pkg_cache == cache_root / "pkg_cache"
