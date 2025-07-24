#
# This hacks around pip's lack of support for supporting environment markers
# when specifying --platform/--abi/--implementation etc
#
# In principle we could just directly patch pip on the robot but that
# has its own share of downsides so we just do this instead
#

import platform
import runpy
import sysconfig
import os
import sys

# TODO: better detection needed to ensure we only run this on systemcore
#       but this is fine for now?
if sysconfig.get_platform() == "linux-aarch64":
    try:
        import pip._vendor.packaging.tags as tags

        def platform_tags():
            for i in reversed(range(17, 38)):
                yield f"manylinux_2_{i}_aarch64"

            yield "linux_systemcore"
            yield "linux_aarch64"

        tags.platform_tags = platform_tags

    except ImportError:
        pass


if __name__ == "__main__":
    # Setup environment for what the SystemCore python would have
    # -> strictly speaking we only care about platform.machine as that's what
    #    we're using in robotpy-meta, but the rest for completeness
    sysconfig.get_platform = lambda: "linux-systemcore"
    platform.machine = lambda: "systemcore"
    platform.python_implementation = lambda: "CPython"
    platform.system = lambda: "Linux"
    platform.python_version = lambda: "3.13.0"

    runpy.run_module("pip", run_name="__main__")
