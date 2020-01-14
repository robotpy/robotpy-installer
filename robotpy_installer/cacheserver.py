import logging
import threading
from http.server import SimpleHTTPRequestHandler

logger = logging.getLogger("robotpy.installer")


class CacheServer:
    def __init__(self, ssh_controller, cache_root):
        self.controller = ssh_controller
        self.cache_root = cache_root

        self.controller.ssh_connect()
        self.transport = self.controller.client.get_transport()
        self.pipe_port = self.transport.request_port_forward("", 0)

    def close(self):
        self.controller.ssh_close_connection()

    def process_request(self, request):
        client_address = request.getpeername()
        try:
            SimpleHTTPRequestHandler(
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

    def handle_requests(self):
        request = self.transport.accept()

        while request is not None:
            t = threading.Thread(target=self.process_request, args=[request])
            t.setDaemon(True)
            t.run()
            request = self.transport.accept()
