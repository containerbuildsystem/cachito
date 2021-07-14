# SPDX-License-Identifier: GPL-3.0-or-later

import json
import logging
import re
import subprocess  # nosec
from typing import Iterator

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config

log = logging.getLogger(__name__)


def run_cmd(cmd, params, exc_msg=None):
    """
    Run the given command with provided parameters.

    :param iter cmd: iterable representing command to be executed
    :param dict params: keyword parameters for command execution
    :param str exc_msg: an optional exception message when the command fails
    :returns: the command output
    :rtype: str
    :raises CachitoError: if the command fails
    """
    params.setdefault("capture_output", True)
    params.setdefault("universal_newlines", True)
    params.setdefault("encoding", "utf-8")

    conf = get_worker_config()
    params.setdefault("timeout", conf.cachito_subprocess_timeout)

    try:
        response = subprocess.run(cmd, **params)  # nosec
    except subprocess.TimeoutExpired as e:
        raise CachitoError(str(e))

    if response.returncode != 0:
        log.error('The command "%s" failed with: %s', " ".join(cmd), response.stderr)
        raise CachitoError(exc_msg or "An unexpected error occurred")

    return response.stdout


def load_json_stream(s: str) -> Iterator:
    """
    Load all JSON objects from input string.

    The objects can be separated by one or more whitespace characters. The return value is
    a generator that will yield the parsed objects one by one.
    """
    decoder = json.JSONDecoder()
    non_whitespace = re.compile(r"\S")
    i = 0

    while match := non_whitespace.search(s, i):
        obj, i = decoder.raw_decode(s, match.start())
        yield obj
