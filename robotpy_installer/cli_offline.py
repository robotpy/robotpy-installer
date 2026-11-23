"""CLI adapters for creating and inspecting offline dependency bundles."""

import argparse
import subprocess
from pathlib import Path
from typing import Optional

from tomli import TOMLDecodeError

from ._bundle_format import BundleError
from .errors import Error
from .offline import create_bundle, inspect_bundle
from .utils import handle_cli_error


class InstallerOfflineCreateBundle:
    """Create an offline dependency zipapp for this host and Python version."""

    def __init__(self, parser: argparse.ArgumentParser):
        parser.add_argument(
            "--pyproject",
            type=Path,
            help="Project configuration (default: project directory's pyproject.toml)",
        )
        parser.add_argument(
            "--output", type=Path, required=True, help="New bundle path"
        )
        parser.add_argument(
            "--find-links", type=Path, help="Additional wheel directory"
        )
        parser.add_argument(
            "--use-certifi", action="store_true", help="Use certifi certificates"
        )

    @handle_cli_error
    def run(
        self,
        project_path: Path,
        pyproject: Optional[Path],
        output: Path,
        find_links: Optional[Path],
        use_certifi: bool,
    ):
        pyproject_path = (
            pyproject if pyproject is not None else project_path / "pyproject.toml"
        )
        try:
            create_bundle(
                pyproject_path,
                output,
                find_links=find_links,
                use_certifi=use_certifi,
            )
        except TOMLDecodeError as exc:
            raise Error(
                f"Invalid TOML in {pyproject_path}: {exc}. "
                "Fix the project configuration and retry."
            ) from exc
        except FileExistsError as exc:
            raise Error(
                f"Cannot create bundle: output already exists: {output}. "
                "Choose a new output path."
            ) from exc
        except (BundleError, OSError, subprocess.CalledProcessError) as exc:
            raise Error(f"Cannot create bundle: {exc}") from exc


class InstallerOfflineInspect:
    """Inspect and verify a bundle without executing its code."""

    def __init__(self, parser: argparse.ArgumentParser):
        parser.add_argument("bundle", type=Path, help="Bundle to inspect")

    @handle_cli_error
    def run(self, bundle: Path):
        try:
            print(inspect_bundle(bundle))
        except (BundleError, OSError) as exc:
            raise Error(f"Cannot inspect bundle: {exc}") from exc


class InstallerOffline:
    """Create and inspect offline dependency bundles."""

    subcommands = [
        ("create-bundle", InstallerOfflineCreateBundle),
        ("inspect", InstallerOfflineInspect),
    ]
