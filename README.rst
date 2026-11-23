robotpy-installer
=================

The RobotPy installer can be used to:

* Install python on your SystemCore
* Install python packages on your SystemCore via pip
* Deploy RobotPy projects to a SystemCore

It can install downloaded packages on your SystemCore without requiring internet
access, which makes it usable in a competition (provided you downloaded the
necessary packages when you were connected to the internet).

To manage only explicitly listed requirements, set ``robotpy_version`` to
``"ignored"`` in your project's ``pyproject.toml``::

    [tool.robotpy]
    robotpy_version = "ignored"
    requires = ["numpy"]

In this mode, sync and deploy do not automatically install or check the RobotPy
package, and ``components`` is ignored. Explicit requirements and their dependencies
are still installed normally. With an empty ``requires`` list, package installation
and deploy's package clearing are skipped, even with ``--force-install``. With
nonempty ``requires``, deploy retains its normal package-clearing behavior
(including respecting ``--no-uninstall``).

Excluding files from deployment
-------------------------------

Add ``deploy_exclude`` to your existing ``[tool.robotpy]`` section in
``pyproject.toml``::

    deploy_exclude = ["tests/", "data/**", "!data/required.json"]

Patterns use gitignore syntax relative to the project root, with forward slashes
on all platforms. Later patterns override earlier ones; ``!`` re-includes files,
provided their parent directories are not excluded. ``.gitignore`` files are not read.

Defaults exclude names starting with a period, ``__pycache__/``, ``ctre_sim/``,
``logs/``, ``venv/``, and ``*.pyc``, ``*.whl``, ``*.ipk``, ``*.zip``, ``*.gz``, ``*.wpilog``. Your patterns
can override these defaults, for example::

    deploy_exclude = ["!logs/", "!*.wpilog"]

Exclusions apply to normal/local deploy and large-file warnings, including with
``--no-install``. Generated ``deploy.json`` metadata is always written.

Deployed Python version
-----------------------

For pinned 2027 RobotPy versions, the installer automatically selects Python:

* Before ``2027.0.0a8``: Python 3.14.9
* ``2027.0.0a8`` and later: Python 3.15.1

When ``robotpy_version = "ignored"``, Python defaults to 3.15. To use 3.14 instead::

    [tool.robotpy]
    robotpy_version = "ignored"
    python_version = "3.14"

``python_version`` accepts only ``"3.14"`` or ``"3.15"`` and is rejected when
``robotpy_version`` is pinned. No locally installed RobotPy package is required
in ignored mode. This setting selects the robot's Python, not the host's Python.
Standalone installer download/install commands also use project configuration;
without a RobotPy project, they default to Python 3.15.

Offline dependency bundles
--------------------------

On an internet-connected computer, bundle the dependencies from your project's
``pyproject.toml`` for offline installation::

    robotpy installer offline create-bundle --output dependencies.pyz

Use ``--pyproject PATH`` to select another project file. The output must not
already exist. To view a bundle's contents without running it::

    robotpy installer offline inspect dependencies.pyz

Copy the bundle to the offline computer, activate your virtual environment,
and run::

    python dependencies.pyz

This installs local dependencies and populates the robot's dependency cache;
it does not copy your project or deploy to the robot. Neither pip nor RobotPy
needs to be preinstalled.

The offline computer must use the same Python major/minor version and
implementation, operating system, and architecture as the creating computer,
with support for the bundled wheels. Only run bundles from publishers you trust.
See each command's ``--help`` for additional options.

For more information about installing and using the RobotPy installer, see 
`http://robotpy.readthedocs.io/en/stable/install/packages.html <http://robotpy.readthedocs.io/en/stable/install/packages.html>`_
