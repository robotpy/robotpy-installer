import contextlib
import hashlib
import json
import logging
import socket
import sys
import urllib.request
from os.path import exists

from robotpy_installer import __version__
from robotpy_installer.errors import Error

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


def _urlretrieve(url, fname, cache):
    # Get it
    print("Downloading", url)

    # Save bandwidth! Use stored metadata to prevent re-downloading
    # stuff we already have
    last_modified = None
    etag = None
    cache_fname = None

    if cache:
        cache_fname = fname + ".jmd"
        if exists(fname) and exists(cache_fname):
            try:
                with open(cache_fname) as fp:
                    md = json.load(fp)
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
        # adapted from urlretrieve source
        headers = {"User-Agent": _useragent}
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        if etag:
            headers["If-None-Match"] = etag

        req = urllib.request.Request(url, headers=headers)

        with contextlib.closing(urllib.request.urlopen(req)) as rfp:
            headers = rfp.info()

            with open(fname, "wb") as fp:

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
                    fp.write(block)
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
    sys.stdout.write("\n")


def _resolve_addr(hostname):
    try:
        logger.debug("Looking up hostname '%s'...", hostname)
        addrs = socket.getaddrinfo(hostname, None)
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
            hostname = ip
            break

    return hostname
