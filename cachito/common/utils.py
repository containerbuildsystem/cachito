# SPDX-License-Identifier: GPL-3.0-or-later
import base64


def b64encode(s: bytes) -> str:
    """Encode a bytes string in base64."""
    return base64.b64encode(s).decode("utf-8")
