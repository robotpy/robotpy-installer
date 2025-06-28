import functools
import contextlib
import hashlib
import json
import logging
import pathlib
import socket
import sys
import typing
import urllib.request
import pathlib

from robotpy_installer import __version__
from .errors import Error

logger = logging.getLogger("robotpy.installer")

_useragent = "robotpy-installer/%s" % __version__


def md5sum(fname):
    md5 = hashlib.md5()
    with open(fname, "rb") as fp:
        buf = fp.read(65536)
        while len(buf) > 0:
            md5.update(buf)
            buf = fp.read(65536)
    return md5.hexdigest()


def _urlretrieve(
    url,
    fname: pathlib.Path,
    cache: bool,
    ssl_context,
    show_status: bool = True,
    reqheaders: typing.Optional[typing.Dict[str, str]] = None,
):
    if show_status:
        # Get it
        print("Downloading", url)

    # Save bandwidth! Use stored metadata to prevent re-downloading
    # stuff we already have
    last_modified = None
    etag = None
    cache_fname = None

    if cache:
        cache_fname = fname.with_suffix(".jmd")
        if fname.exists() and cache_fname.exists():
            try:
                with open(cache_fname) as cfp:
                    md = json.load(cfp)
                if md5sum(fname) == md["md5"]:
                    etag = md.get("etag")
                    last_modified = md.get("last-modified")
            except Exception:
                pass

    blocksize = 1024 * 8

    def _reporthook(read, totalsize):
        if totalsize > 0:
            percent = min(int(read * 100 / totalsize), 100)
            sys.stdout.write("\r%02d%%" % percent)
        else:
            sys.stdout.write("\r%dbytes" % read)
        sys.stdout.flush()

    try:
        if reqheaders:
            reqheaders = reqheaders.copy()
        else:
            reqheaders = {}

        # adapted from urlretrieve source
        reqheaders["User-Agent"] = _useragent
        if last_modified:
            reqheaders["If-Modified-Since"] = last_modified
        if etag:
            reqheaders["If-None-Match"] = etag

        req = urllib.request.Request(url, headers=reqheaders)

        with contextlib.closing(
            urllib.request.urlopen(req, context=ssl_context)
        ) as rfp:
            headers = rfp.info()

            with open(fname, "wb") as dfp:
                # Deal with header stuff
                size = -1
                read = 0
                if "content-length" in headers:
                    size = int(headers["Content-Length"])

                while True:
                    block = rfp.read(blocksize)
                    if not block:
                        break
                    read += len(block)
                    dfp.write(block)

                    if show_status:
                        _reporthook(read, size)

        if size >= 0 and read < size:
            raise ValueError("Only retrieved %s of %s bytes" % (read, size))

        # If we received info from the server, cache it
        if cache_fname:
            md = {}
            if "etag" in headers:
                md["etag"] = headers["ETag"]
            if "last-modified" in headers:
                md["last-modified"] = headers["Last-Modified"]
            if md:
                md["md5"] = md5sum(fname)
                with open(cache_fname, "w") as fp:
                    json.dump(md, fp)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            if show_status:
                sys.stdout.write("Not modified")
        else:
            raise
    except Exception as e:
        if "certificate verify failed" in str(e) and sys.platform == "darwin":
            pyver = ".".join(map(str, sys.version_info[:2]))
            msg = (
                "SSL certificates are not installed! Run /Applications/Python %s/Install Certificates.command to fix this"
                % pyver
            )
            raise Exception(msg) from e
        else:
            raise e
    if show_status:
        sys.stdout.write("\n")


def _resolve_addr(hostname):
    try:
        logger.debug("Looking up hostname '%s'...", hostname)
        # Note: Windows will never return a SOCK_STREAM address if you don't explicitly
        #       ask for it here. macOS and Linux always return all types, but filter it
        #       for us if we specify it here, so it doesn't hurt to specify it.
        addrs = socket.getaddrinfo(hostname, None, 0, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise Error("Could not find robot at %s" % hostname) from e

    # Sort the address by family.
    # Lucky for us, the family type is the first element of the tuple, and it's an enumerated type with
    # AF_INET=2 (IPv4) and AF_INET6=23 (IPv6), so sorting them will provide us with the AF_INET address first.
    addrs.sort()

    # pick the first address that is sock_stream
    # AF_INET sockaddr tuple:  (address, port)
    # AF_INET6 sockaddr tuple: (address, port, flow info, scope id)
    for _, socktype, _, _, sockaddr in addrs:
        if socktype == socket.SOCK_STREAM:
            ip = sockaddr[
                0
            ]  # The address if the first tuple element for both AF_INET and AF_INET6
            logger.debug("-> Found %s at %s" % (hostname, ip))
            return ip

    raise Error("Could not find robot at %s" % hostname)


def print_err(*args):
    print(*args, file=sys.stderr)


def yesno(prompt: str) -> bool:
    """Returns True if user answers 'y'"""
    prompt += " [y/n]"
    a = ""
    while a not in ["y", "n"]:
        a = input(prompt).lower()

    return a == "y"


def handle_cli_error(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Error as e:
            print(f"ERROR:", e, file=sys.stderr)
            return False

    return wrapper


def exists_case_sensitive(path: pathlib.Path) -> bool:
    """
    case sensitive replacement for pathlib.Path.exists().
    This only checks the file or dir at the end of the path exists and has correct case.
    This is required because Windows by default does not check case.
    In the case where the path ends in '..' and the directory exists then True is returned.
    Do NOT .resolve() the path before calling.
    """

    # exit if the path does not exist; continue to confim the case
    if not path.exists():
        return False

    # resolve makes the path object have the accurate capitilization of each part
    resolved_path = path.resolve()

    if path.is_file():
        if resolved_path.name == path.name:
            return True

        return False

    elif path.is_dir():
        # this will get rid of a '.' at the end of a path
        absolute_path = path.absolute()

        # no case check nessisary if true
        if absolute_path.parts[-1] == "..":
            return True

        if resolved_path.parts[-1] == absolute_path.parts[-1]:
            return True

        return False

    # if neither file or directory: False; like Path.exists()
    return False
