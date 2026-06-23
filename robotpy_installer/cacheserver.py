import logging
import pathlib
import threading
from http.server import SimpleHTTPRequestHandler
from typing import Dict

logger = logging.getLogger("cacheserver")


class HTTPHandler(SimpleHTTPRequestHandler):
    def __init__(self, mapped_files, *args, **kwargs):
        self.mapped_files = mapped_files
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args) -> None:
        logger.debug(f"%s {format}", self.address_string(), *args)

    def translate_path(self, path):
        xpath = path.split("?", 1)[0]
        xpath = xpath.split("#", 1)[0]
        redirect = self.mapped_files.get(xpath)
        if redirect:
            return redirect

        return super().translate_path(path)


class CacheServer:
    def __init__(self, ssh_controller, cache_root: pathlib.Path):
        self.controller = ssh_controller
        self.cache_root = cache_root
        self.mapped_files: Dict[str, str] = {}
        self._closed = threading.Event()
        self.port = self.controller.cache_listen()

    def add_mapping(self, fname: str, local_file: str):
        self.mapped_files[fname] = local_file

    def start(self):
        t = threading.Thread(target=self._handle_requests)
        t.daemon = True
        t.start()

    def close(self):
        self._closed.set()
        self.controller.cache_close()

    def process_request(self, request):
        client_address = request.getpeername()
        try:
            HTTPHandler(
                self.mapped_files,
                request=request,
                client_address=client_address,
                server=None,
                directory=self.cache_root,
            ).handle()
        except (OSError, ValueError) as e:
            if str(e) in ("File is closed", "readline of closed file"):
                return
            raise
        finally:
            request.close()

    def _handle_requests(self):
        while not self._closed.is_set():
            try:
                request = self.controller.cache_accept()
            except OSError:
                if self._closed.is_set():
                    return
                raise

            if request is None:
                return

            if self._closed.is_set():
                request.close()
                return

            t = threading.Thread(target=self.process_request, args=[request])
            t.daemon = True
            t.start()
