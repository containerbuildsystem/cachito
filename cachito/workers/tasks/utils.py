# SPDX-License-Identifier: GPL-3.0-or-later
import base64
from pathlib import Path
from typing import Union

__all__ = ["make_base64_config_file"]


def make_base64_config_file(content: str, dest_relpath: Union[str, Path]) -> dict:
    """
    Make a dict to be added as a base64-encoded config file to a request.

    :param str content: content of config file
    :param (str | Path) dest_relpath: relative path to config file from root of bundle directory
    :return: dict with "content", "path" and "type" keys
    """
    return {
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "path": str(dest_relpath),
        "type": "base64",
    }
