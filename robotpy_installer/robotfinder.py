import logging
import socket
import threading
import typing

from robotpy_installer.utils import _resolve_addr

logger = logging.getLogger("robotpy.installer")


class RobotFinder:
    def __init__(self, *addrs):
        self.tried = 0
        self.answer = None
        self.addrs = addrs
        self.cond = threading.Condition()

    def find(self) -> typing.Optional[typing.Tuple[str, socket.socket]]:

        with self.cond:
            self.tried = 0
            for addr, resolve in self.addrs:
                t = threading.Thread(target=self._try_server, args=(addr, resolve))
                t.setDaemon(True)
                t.start()

            while self.answer is None and self.tried != len(self.addrs):
                self.cond.wait()

            if self.answer:
                logger.info("-> Robot is at %s", self.answer[0])

            return self.answer

    def _try_server(self, addr: str, resolve: bool):
        success = False
        conn = None
        try:
            if resolve:
                addr = _resolve_addr(addr)

            conn = socket.create_connection((addr, 22), timeout=10)
            success = True
        except Exception:
            pass

        with self.cond:
            self.tried += 1
            if success and not self.answer:
                self.answer = (addr, conn)
            elif conn is not None:
                conn.close()

            self.cond.notify_all()
