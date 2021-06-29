# SPDX-License-Identifier: GPL-3.0-or-later
import collections
import hashlib
import logging
import os
from typing import Dict

import requests

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config

__all__ = [
    "update_request_with_config_files",
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


def update_request_env_vars(request_id: int, env_vars: Dict[str, Dict[str, str]]) -> None:
    """Update environment variables of a request.

    :param int request_id: the id of a request to update the environment variables.
    :param dict env_vars: mapping of environment variables to record. The keys represent
        the environment variable name, and its value should be another map with the "value" and
        "kind" attributes, e.g. {"NAME": {"value": "VALUE", "kind": "KIND"}}.
    :raise CachitoError: if the request to the Cachito API fails
    """
    from cachito.workers.requests import requests_auth_session

    config = get_worker_config()
    request_url = _get_request_url(request_id)
    payload = {"environment_variables": env_vars}
    try:
        rv = requests_auth_session.patch(
            request_url, json=payload, timeout=config.cachito_api_timeout
        )
    except requests.RequestException:
        msg = (
            f"The connection failed when updating environment variables on the request {request_id}"
        )
        log.exception(msg)
        raise CachitoError(msg)
    if not rv.ok:
        log.error(
            "The worker failed to update environment variables on the request %d. "
            "The status was %d. The text was:\n%s",
            request_id,
            rv.status_code,
            rv.text,
        )
        raise CachitoError(f"Updating environment variables on request {request_id} failed")


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


def download_binary_file(url, download_path, auth=None, insecure=False, chunk_size=8192):
    """
    Download a binary file (such as a TAR archive) from a URL.

    :param str url: URL for file download
    :param (str | Path) download_path: Path to download file to
    :param requests.auth.AuthBase auth: Authentication for the URL
    :param bool insecure: Do not verify SSL for the URL
    :param int chunk_size: Chunk size param for Response.iter_content()
    :raise CachitoError: If download failed
    """
    # Import this here to avoid a circular import
    from cachito.workers.requests import requests_session

    try:
        resp = requests_session.get(url, stream=True, verify=not insecure, auth=auth)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise CachitoError(f"Could not download {url}: {e}")

    with open(download_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=chunk_size):
            f.write(chunk)
