import os
import string
from collections import OrderedDict
from dataclasses import dataclass
from distutils.version import LooseVersion
from functools import reduce as _reduce
from os.path import exists, join, basename
from typing import Dict, Iterable, List, Set, Sequence, Tuple

from robotpy_installer.errors import OpkgError
from robotpy_installer.utils import _urlretrieve, md5sum

Package = OrderedDict


@dataclass
class Feed:
    url: str
    db_fname: str
    pkgs: Dict[str, List[Package]]
    loaded: bool


class OpkgRepo(object):
    """Simplistic OPkg Manager"""

    sys_packages = ["libc6"]

    def __init__(self, opkg_cache, arch: str, ssl_context):
        self.feeds: List[Feed] = []
        self.opkg_cache = opkg_cache
        self.arch = arch
        self.ssl_context = ssl_context
        if not exists(self.opkg_cache):
            os.makedirs(self.opkg_cache)
        self.pkg_dbs = join(self.opkg_cache, "Packages")
        if not exists(self.pkg_dbs):
            os.makedirs(self.pkg_dbs)

    def add_feed(self, url: str) -> None:
        # Snippet from https://gist.github.com/seanh/93666
        valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
        safe_url = "".join(c for c in url if c in valid_chars)
        safe_url = safe_url.replace(" ", "_")
        feed = Feed(
            url,
            db_fname=join(self.pkg_dbs, safe_url),
            pkgs=OrderedDict(),
            loaded=False,
        )
        if exists(feed.db_fname):
            self.load_package_db(feed)
            feed.loaded = True

        self.feeds.append(feed)

    def update_packages(self) -> None:
        for feed in self.feeds:
            pkgurl = feed.url + "/Packages"
            _urlretrieve(pkgurl, feed.db_fname, True, self.ssl_context)
            self.load_package_db(feed)

    def load_package_db(self, feed: Feed) -> None:

        # dictionary of lists of packages sorted by version
        pkg: Package = OrderedDict()
        with open(feed.db_fname, "r", encoding="utf-8") as fp:
            for line in fp.readlines():
                line = line.strip()
                if len(line) == 0:
                    self._add_pkg(pkg, feed)
                    pkg = OrderedDict()
                else:
                    if ":" in line:
                        k, v = [i.strip() for i in line.split(":", 1)]
                        if k == "Version":
                            pkg[k] = LooseVersion(v)
                        else:
                            pkg[k] = v

        self._add_pkg(pkg, feed)

        # Finally, make sure all the packages are sorted by version
        for pkglist in feed.pkgs.values():
            pkglist.sort(key=lambda p: p["Version"])

    def _add_pkg(self, pkg: Package, feed: Feed) -> None:
        if len(pkg) == 0 or pkg.get("Architecture", None) != self.arch:
            return
        # Add download url and fname
        if "Filename" in pkg:
            pkg["url"] = "/".join((feed.url, pkg["Filename"]))

        # Only retain one version of a package
        pkgs = feed.pkgs.setdefault(pkg["Package"], [])
        for old_pkg in pkgs:
            if old_pkg["Version"] == pkg["Version"]:
                old_pkg.clear()
                old_pkg.update(pkg)
                break
        else:
            pkgs.append(pkg)

    def get_pkginfo(self, name: str):
        loaded = False
        for feed in self.feeds:
            loaded = loaded or feed.loaded
            if name in feed.pkgs:
                return feed.pkgs[name][-1]

        if loaded:
            msg = "Package %s is not in the package list (did you misspell it?)" % name
        else:
            msg = "There are no package lists, did you download %s yet?" % name

        raise OpkgError(msg)

    def _get_pkg_fname(self, pkg: Package) -> str:
        return join(self.opkg_cache, basename(pkg["Filename"]))

    def _get_pkg_deps(self, name: str) -> Set[str]:
        info = self.get_pkginfo(name)
        if "Depends" in info:
            return {
                dep
                for dep in [
                    dep.strip().split(" ", 1)[0] for dep in info["Depends"].split(",")
                ]
                if dep not in self.sys_packages
            }
        return set()

    def get_cached_pkg(self, name: str) -> Tuple:
        """Returns the pkg, filename of a cached package"""
        pkg = self.get_pkginfo(name)
        fname = self._get_pkg_fname(pkg)

        if not exists(fname):
            raise OpkgError("Package '%s' has not been downloaded" % name)

        if not md5sum(fname) == pkg["MD5Sum"]:
            raise OpkgError("md5sum of package '%s' md5sum does not match" % name)

        return pkg, fname

    def resolve_pkg_deps(self, packages: Sequence[str]) -> List[str]:
        """Given a list of package(s) desired to be installed, topologically
        sorts them by dependencies and returns an ordered list of packages"""

        pkgs = {}
        packages = list(packages)

        for pkg in packages:
            if pkg in pkgs:
                continue
            deps = self._get_pkg_deps(pkg)
            pkgs[pkg] = deps
            packages.extend(deps)

        retval: List[str] = []
        for results in self._toposort(pkgs):
            retval.extend(results)

        return retval

    @classmethod
    def _toposort(cls, data: Dict[str, Set[str]]) -> Iterable[Set[str]]:
        # Copied from https://bitbucket.org/ericvsmith/toposort/src/25b5894c4229cb888f77cf0c077c05e2464446ac/toposort.py?at=default
        # -> Apache 2.0 license, Copyright 2014 True Blade Systems, Inc.

        # Special case empty input.
        if len(data) == 0:
            return

        # Copy the input so as to leave it unmodified.
        data = data.copy()

        # Ignore self dependencies.
        for k, v in data.items():
            v.discard(k)
        # Find all items that don't depend on anything.
        extra_items_in_deps = _reduce(set.union, data.values()) - set(data.keys())
        # Add empty dependences where needed.
        data.update({item: set() for item in extra_items_in_deps})
        while True:
            ordered = {item for item, dep in data.items() if len(dep) == 0}
            if not ordered:
                break
            yield ordered
            data = {
                item: (dep - ordered)
                for item, dep in data.items()
                if item not in ordered
            }
        if len(data) != 0:
            yield cls._modified_dfs(data)

    @staticmethod
    def _modified_dfs(nodes: Dict[str, Set[str]]):
        # this is a modified depth first search that does a best effort at
        # a topological sort, but ignores cycles and keeps going on despite
        # that. Only used if the topological sort fails.
        retval = []
        visited = set()

        def _visit(n):
            if n in visited:
                return

            visited.add(n)
            for m in nodes[n]:
                _visit(m)

            retval.append(n)

        for item in nodes:
            _visit(item)

        return retval

    def download(self, name: str) -> str:

        pkg = self.get_pkginfo(name)
        fname = self._get_pkg_fname(pkg)

        # Only download it if necessary
        if not exists(fname) or not md5sum(fname) == pkg["MD5Sum"]:
            _urlretrieve(pkg["url"], fname, True, self.ssl_context)
        # Validate it
        if md5sum(fname) != pkg["MD5Sum"]:
            raise OpkgError("Downloaded package for %s md5sum does not match" % name)

        return fname

    def load_opkg_from_req(self, *files: str) -> List[str]:
        """
        Pull the list of opkgs from a requirements.txt-like file
        """
        opkgs = []
        # Loop through the passed in files to support multiple requirements files
        for file in files:
            with open(file, "r") as f:
                for row in f:
                    # Ignore commented lines and empty lines
                    stripped = row.strip()
                    if stripped and not stripped.startswith("#"):
                        # Add the package to the list of packages (and remove leading and trailing whitespace)
                        opkgs.append(stripped)
        return opkgs
