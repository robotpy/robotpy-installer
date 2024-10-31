#
# This hacks around pip's lack of support for supporting environment markers
# when specifying --platform/--abi/--implementation etc
#

import platform
import runpy
import sysconfig
import os
import sys

rio_python_version = "3.12.0"


if __name__ == "__main__":
    # Setup environment for what the RoboRIO python would have
    # -> strictly speaking we only care about platform.machine as that's what
    #    we're using in robotpy-meta, but the rest for completeness
    sysconfig.get_platform = lambda: "linux_roborio"
    platform.machine = lambda: "roborio"
    platform.python_implementation = lambda: "CPython"
    platform.system = lambda: "Linux"
    platform.python_version = lambda: rio_python_version

    runpy.run_module("pip", run_name="__main__")
