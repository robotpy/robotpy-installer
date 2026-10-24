"""Supported Python distributions for the robot (independent of host Python)."""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class PythonRuntime:
    version: Tuple[int, int, int]
    url: str

    @property
    def version_tuple(self) -> Tuple[int, int]:
        return self.version[:2]

    @property
    def version_num(self) -> str:
        return "".join(map(str, self.version_tuple))


PYTHON_314 = PythonRuntime(
    (3, 14, 3),
    "https://github.com/astral-sh/python-build-standalone/releases/download/20260310/cpython-3.14.3+20260310-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz",
)
PYTHON_315 = PythonRuntime(
    (3, 15, 1),
    "https://github.com/astral-sh/python-build-standalone/releases/download/20261021/cpython-3.15.1%2B20261021-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz",
)
DEFAULT_RUNTIME = PYTHON_315
