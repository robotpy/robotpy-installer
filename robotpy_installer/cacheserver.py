import logging
import pathlib
import threading
from http.server import SimpleHTTPRequestHandler

from robotpy_installer.sshcontroller import SshController

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
    def __init__(self, ssh_controller: SshController, cache_root: pathlib.Path):
        self.controller = ssh_controller
        self.cache_root = cache_root

        self.transport = self.controller.client.get_transport()
        self.port = self.transport.request_port_forward("", 0)

        self.mapped_files = {}

    def add_mapping(self, fname: str, local_file: pathlib.Path):
        self.mapped_files[fname] = local_file

    def start(self):
        t = threading.Thread(target=self._handle_requests)
        t.setDaemon(True)
        t.start()

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
        except OSError as e:
            if str(e) == "File is closed":
                return
        finally:
            request.close()

    def _handle_requests(self):
        request = self.transport.accept()

        while request is not None:
            t = threading.Thread(target=self.process_request, args=[request])
            t.setDaemon(True)
            t.start()

            request = self.transport.accept()
