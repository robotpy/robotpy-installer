class Error(Exception):
    pass


class ArgError(Error):
    pass


class SshExecError(Error):
    def __init__(self, msg, retval):
        super().__init__(msg)
        self.retval = retval


class OpkgError(Exception):
    pass
