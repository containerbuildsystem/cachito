# SPDX-License-Identifier: GPL-3.0-or-later
from cachito.errors import CachitoError


class NexusScriptError(CachitoError):
    """An error was encountered while executing a Nexus script."""


class CachitoCalledProcessError(CachitoError):
    """Command executed with subprocess.run() returned non-zero value."""

    def __init__(self, err_msg: str, retcode: int):
        """Initialize the error with a message and the return code of the failing command."""
        super().__init__(err_msg)
        self.retcode = retcode
