# SPDX-License-Identifier: GPL-3.0-or-later
import collections
import hashlib
import logging
import os
import subprocess

import requests

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config

__all__ = [
    "run_cmd",
    "update_request_with_config_files",
    "update_request_with_deps",
    "update_request_with_package",
    "verify_checksum",
    "ChecksumInfo",
]

log = logging.getLogger(__name__)

ChecksumInfo = collections.namedtuple("ChecksumInfo", "algorithm hexdigest")


def _get_request_url(request_id):
    """
    Get the API URL for the Cachito request.

    :param int request_id: the request ID to use when constructing the API URL
    :return: the API URL of the Cachito request
    :rtype: str
    """
    config = get_worker_config()
    return f'{config.cachito_api_url.rstrip("/")}/requests/{request_id}'


def update_request_with_config_files(request_id, config_files):
    """
    Update the Cachito request with the input configuration files.

    :param list config_files: the list of configuration files to add to the request
    :raise CachitoError: if the request to the Cachito API fails
    """
    # Import this here to avoid a circular import
    from cachito.workers.requests import requests_auth_session

    log.info("Adding %d configuration files to the request %d", len(config_files), request_id)
    config = get_worker_config()
    request_url = _get_request_url(request_id) + "/configuration-files"

    try:
        rv = requests_auth_session.post(
            request_url, json=config_files, timeout=config.cachito_api_timeout
        )
    except requests.RequestException:
        msg = f"The connection failed when adding configuration files to the request {request_id}"
        log.exception(msg)
        raise CachitoError(msg)

    if not rv.ok:
        log.error(
            "The worker failed to add configuration files to the request %d. The status was %d. "
            "The text was:\n%s",
            request_id,
            rv.status_code,
            rv.text,
        )
        raise CachitoError(f"Adding configuration files on request {request_id} failed")


def update_request_with_deps(request_id, package, deps):
    """
    Update the Cachito request with the resolved dependencies.

    :param int request_id: the ID of the Cachito request
    :param dict package: the package these dependencies are associated with
    :param list deps: the list of dependency dictionaries to record
    :raise CachitoError: if the request to the Cachito API fails
    """
    # Import this here to avoid a circular import
    from cachito.workers.requests import requests_auth_session

    config = get_worker_config()
    request_url = _get_request_url(request_id)

    log.info("Adding %d dependencies to request %d", len(deps), request_id)
    for index in range(0, len(deps), config.cachito_deps_patch_batch_size):
        batch_upper_limit = index + config.cachito_deps_patch_batch_size
        payload = {"dependencies": deps[index:batch_upper_limit], "package": package}
        try:
            log.info(
                "Patching deps {} through {} out of {}".format(
                    index + 1, min(batch_upper_limit, len(deps)), len(deps)
                )
            )
            rv = requests_auth_session.patch(
                request_url, json=payload, timeout=config.cachito_api_timeout
            )
        except requests.RequestException:
            msg = f"The connection failed when setting the dependencies on request {request_id}"
            log.exception(msg)
            raise CachitoError(msg)

        if not rv.ok:
            log.error(
                "The worker failed to set the dependencies on request %d. The status was %d. "
                "The text was:\n%s",
                request_id,
                rv.status_code,
                rv.text,
            )
            raise CachitoError(f"Setting the dependencies on request {request_id} failed")


def update_request_with_package(request_id, package, env_vars=None):
    """
    Update the request with the resolved packages and corresponding metadata.

    :param dict package: the package that was resolved
    :param dict env_vars: mapping of environment variables to record. The keys represent
        the environment variable name, and its value should be another map wth the "value" and
        "kind" attributes, e.g. {"NAME": {"value": "VALUE", "kind": "KIND"}}.
    :raise CachitoError: if the request to the Cachito API fails
    """
    log.info('Adding the package "%r" to the request %d', package, request_id)
    payload = {"package": package}

    if env_vars:
        log.info("Also adding environment variables to the request %d: %s", request_id, env_vars)
        payload["environment_variables"] = env_vars

    # Import this here to avoid a circular import
    from cachito.workers.requests import requests_auth_session

    config = get_worker_config()
    request_url = _get_request_url(request_id)

    try:
        rv = requests_auth_session.patch(
            request_url, json=payload, timeout=config.cachito_api_timeout
        )
    except requests.RequestException:
        msg = f"The connection failed when adding a package to the request {request_id}"
        log.exception(msg)
        raise CachitoError(msg)

    if not rv.ok:
        log.error(
            "The worker failed to add a package to the request %d. The status was %d. "
            "The text was:\n%s",
            request_id,
            rv.status_code,
            rv.text,
        )
        raise CachitoError(f"Setting a package on request {request_id} failed")


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

    response = subprocess.run(cmd, **params)

    if response.returncode != 0:
        log.error('The command "%s" failed with: %s', " ".join(cmd), response.stderr)
        raise CachitoError(exc_msg or "An unexpected error occurred")

    return response.stdout


def verify_checksum(file_path, checksum_info, chunk_size=10240):
    """
    Verify the checksum of the file at the given path matches the expected checksum info.

    :param str file_path: the path to the file to be verified
    :param ChecksumInfo checksum_info: the expected checksum information
    :param int chunk_size: the amount of bytes to read at a time
    :raise CachitoError: if the checksum is not as expected
    """
    filename = os.path.basename(file_path)
    try:
        hasher = hashlib.new(checksum_info.algorithm)
    except ValueError as exc:
        msg = f"Cannot perform checksum on the file {filename}, {exc}"
        log.exception(msg)
        raise CachitoError(msg)

    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    computed_hexdigest = hasher.hexdigest()

    if computed_hexdigest != checksum_info.hexdigest:
        msg = (
            f"The file {filename} has an unexpected checksum value, "
            f"expected {checksum_info.hexdigest} but computed {computed_hexdigest}"
        )
        log.error(msg)
        raise CachitoError(msg)
