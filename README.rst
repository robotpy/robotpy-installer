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

Projects can run local commands before deployment by adding one or more
``[[tool.robotpy.deploy]]`` tables. Commands are required by default, so a nonzero
exit status stops the deployment. Set ``required = false`` to report a failure and
continue::

    [[tool.robotpy.deploy]]
    command = ["python", "-m", "mypy"]
    required = false

For more information about installing and using the RobotPy installer, see 
`http://robotpy.readthedocs.io/en/stable/install/packages.html <http://robotpy.readthedocs.io/en/stable/install/packages.html>`_
