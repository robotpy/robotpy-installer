#!/usr/bin/env python3

from setuptools import setup, find_packages

import sys
if sys.version_info[0] < 3:
    sys.stderr.write("ERROR: robotpy-installer requires python 3!")
    exit(1)

from os.path import abspath, dirname, join

installer_ns = {
    '__file__': abspath(join(dirname(__file__), 'robotpy_installer', 'installer.py'))
}
with open(installer_ns['__file__']) as fp:
    exec(fp.read(), installer_ns)

__version__ = installer_ns['__version__']

# Download putty
installer_ns['ensure_win_bins'](ignore_os=True)

with open(join(dirname(__file__), 'README.rst'), 'r') as readme_file:
    long_description = readme_file.read()

setup(name='robotpy-installer',
      version=__version__,
      description='Installation utility program for RobotPy',
      long_description=long_description,
      author='Dustin Spicuzza',
      author_email='robotpy@googlegroups.com',
      url='https://github.com/robotpy/robotpy-installer',
      license='BSD',
      packages=find_packages(),
      package_data={'robotpy_installer': ['win32/plink.exe',
                                          'win32/psftp.exe',
                                          'win32/putty_license.txt']},
      classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Topic :: Software Development'
        ],
      entry_points={
        'console_scripts': ['robotpy-installer=robotpy_installer.installer:main']
      },
)
