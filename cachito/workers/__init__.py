# SPDX-License-Identifier: GPL-3.0-or-later

import json
import logging
import re
import subprocess  # nosec
from pathlib import Path
from tarfile import ExtractError, TarFile
from typing import Iterator

from cachito.errors import SubprocessCallError
from cachito.workers.config import get_worker_config
from cachito.workers.errors import CachitoCalledProcessError

log = logging.getLogger(__name__)


def run_cmd(cmd, params, exc_msg=None):
    """
    Run the given command with provided parameters.

    :param iter cmd: iterable representing command to be executed
    :param dict params: keyword parameters for command execution
    :param str exc_msg: an optional exception message when the command fails
    :returns: the command output
    :rtype: str
    :raises SubprocessCallError: if the command fails
    """
    params.setdefault("capture_output", True)
    params.setdefault("universal_newlines", True)
    params.setdefault("encoding", "utf-8")

    conf = get_worker_config()
    params.setdefault("timeout", conf.cachito_subprocess_timeout)

    try:
        response = subprocess.run(cmd, **params)  # nosec
    except subprocess.TimeoutExpired as e:
        raise SubprocessCallError(str(e))

    if response.returncode != 0:
        log.error('The command "%s" failed with: %s', " ".join(cmd), response.stderr)
        raise CachitoCalledProcessError(
            exc_msg or "An unexpected error occurred", response.returncode
        )

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


def safe_extract(tar: TarFile, path: str = ".", *, numeric_owner: bool = False):
    """
    CVE-2007-4559 replacement for extract() or extractall().

    By using extract() or extractall() on a tarfile object without sanitizing input,
    a maliciously crafted .tar file could perform a directory path traversal attack.
    The patch essentially checks to see if all tarfile members will be
    extracted safely and throws an exception otherwise.

    :param tarfile tar: the tarfile to be extracted.
    :param str path: specifies a different directory to extract to.
    :param numeric_owner: if True, only the numbers for user/group names are used and not the names.
    :raise ExtractError: if there is a Traversal Path Attempt in the Tar File.
    """
    abs_path = Path(path).resolve()
    for member in tar.getmembers():

        member_path = Path(path).joinpath(member.name)
        abs_member_path = member_path.resolve()

        if not abs_member_path.is_relative_to(abs_path):
            raise ExtractError("Attempted Path Traversal in Tar File")

    tar.extractall(path, numeric_owner=numeric_owner)
