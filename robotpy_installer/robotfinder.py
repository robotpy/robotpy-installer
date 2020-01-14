import logging
import socket
import threading

from robotpy_installer.utils import _resolve_addr

logger = logging.getLogger("robotpy.installer")


class RobotFinder:
    def __init__(self, *addrs):
        self.tried = 0
        self.answer = None
        self.addrs = addrs
        self.cond = threading.Condition()

    def find(self):

        with self.cond:
            self.tried = 0
            for addr, resolve in self.addrs:
                t = threading.Thread(target=self._try_server, args=(addr, resolve))
                t.setDaemon(True)
                t.start()

            while self.answer is None and self.tried != len(self.addrs):
                self.cond.wait()

            if self.answer:
                logger.info("-> Robot is at %s", self.answer)
                return self.answer

    def _try_server(self, addr, resolve):
        success = False
        try:
            if resolve:
                addr = _resolve_addr(addr)
            else:
                sd = socket.create_connection((addr, 22), timeout=10)
                sd.close()

            success = True
        except Exception:
            pass

        with self.cond:
            self.tried += 1
            if success and not self.answer:
                self.answer = addr

            self.cond.notify_all()
