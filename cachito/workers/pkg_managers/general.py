# SPDX-License-Identifier: GPL-3.0-or-later
import collections
import logging
import os
import urllib
from typing import Dict

import requests

from cachito.common.checksum import hash_file
from cachito.errors import InvalidChecksum, InvalidRequestData, NetworkError, UnknownHashAlgorithm
from cachito.workers import nexus
from cachito.workers.config import get_worker_config
from cachito.workers.requests import (
    SAFE_REQUEST_METHODS,
    get_requests_session,
    requests_auth_session,
)

__all__ = [
    "update_request_with_config_files",
    "verify_checksum",
    "ChecksumInfo",
]

log = logging.getLogger(__name__)

ChecksumInfo = collections.namedtuple("ChecksumInfo", "algorithm hexdigest")

pkg_requests_session = get_requests_session(retry_options={"allowed_methods": SAFE_REQUEST_METHODS})


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
    :raise NetworkError: if connection fails
    :raise InvalidRequestData: if the request to the Cachito API fails
    """
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
        raise NetworkError(msg)

    if not rv.ok:
        log.error(
            "The worker failed to add configuration files to the request %d. The status was %d. "
            "The text was:\n%s",
            request_id,
            rv.status_code,
            rv.text,
        )
        raise InvalidRequestData(f"Adding configuration files on request {request_id} failed")


def update_request_env_vars(request_id: int, env_vars: Dict[str, Dict[str, str]]) -> None:
    """Update environment variables of a request.

    :param int request_id: the id of a request to update the environment variables.
    :param dict env_vars: mapping of environment variables to record. The keys represent
        the environment variable name, and its value should be another map with the "value" and
        "kind" attributes, e.g. {"NAME": {"value": "VALUE", "kind": "KIND"}}.
    :raise NetworkError: if connection fails
    :raise InvalidRequestData: if the request to the Cachito API fails
    """
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
        raise NetworkError(msg)
    if not rv.ok:
        log.error(
            "The worker failed to update environment variables on the request %d. "
            "The status was %d. The text was:\n%s",
            request_id,
            rv.status_code,
            rv.text,
        )
        raise InvalidRequestData(f"Updating environment variables on request {request_id} failed")


def verify_checksum(file_path: str, checksum_info: ChecksumInfo, chunk_size: int = 10240):
    """
    Verify the checksum of the file at the given path matches the expected checksum info.

    :param str file_path: the path to the file to be verified
    :param ChecksumInfo checksum_info: the expected checksum information
    :param int chunk_size: the amount of bytes to read at a time
    :raise InvalidChecksum: if the checksum is not as expected
    """
    filename = os.path.basename(file_path)

    try:
        hasher = hash_file(file_path, chunk_size, checksum_info.algorithm)
    except UnknownHashAlgorithm as exc:
        msg = f"Cannot perform checksum on the file {filename}, {exc}"
        raise InvalidChecksum(msg)

    computed_hexdigest = hasher.hexdigest()

    if computed_hexdigest != checksum_info.hexdigest:
        msg = (
            f"The file {filename} has an unexpected checksum value, "
            f"expected {checksum_info.hexdigest} but computed {computed_hexdigest}"
        )
        raise InvalidChecksum(msg)


def download_binary_file(url, download_path, auth=None, insecure=False, chunk_size=8192):
    """
    Download a binary file (such as a TAR archive) from a URL.

    :param str url: URL for file download
    :param (str | Path) download_path: Path to download file to
    :param requests.auth.AuthBase auth: Authentication for the URL
    :param bool insecure: Do not verify SSL for the URL
    :param int chunk_size: Chunk size param for Response.iter_content()
    :raise NetworkError: If download failed
    """
    try:
        resp = pkg_requests_session.get(url, stream=True, verify=not insecure, auth=auth)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise NetworkError(f"Could not download {url}: {e}")

    with open(download_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=chunk_size):
            f.write(chunk)


def download_raw_component(raw_component_name, raw_repo_name, download_path, nexus_auth):
    """
    Download raw component if present in raw repo.

    :return: True if component was downloaded, False otherwise
    """
    log.debug("Looking for raw component %r in %r repo", raw_component_name, raw_repo_name)
    download_url = nexus.get_raw_component_asset_url(raw_repo_name, raw_component_name)

    if download_url is not None:
        log.debug("Found raw component, will download from %r", download_url)
        download_binary_file(download_url, download_path, auth=nexus_auth)
        return True

    return False


def upload_raw_package(repo_name, artifact_path, dest_dir, filename, is_request_repository):
    """
    Upload a raw package to a Nexus repository.

    :param str repo_name: the name of the hosted raw repository to upload the package to
    :param str artifact_path: the path of the raw package to be uploaded
    :param str dest_dir: the path of the directory to where the raw package will be uploaded
        to in the Nexus repository
    :param str filename: the name to save the file with after it is uploaded to the dest_dir
    :param bool is_request_repository: whether to use the cachito nexus instance or the hoster one,
        if available
    """
    components = [{"path": artifact_path, "filename": filename}]
    to_nexus_hoster = not is_request_repository
    log.debug("Uploading %r as a raw package to the %r Nexus repository", artifact_path, repo_name)
    nexus.upload_raw_component(repo_name, dest_dir, components, to_nexus_hoster)


def extract_git_info(vcs_url):
    """
    Extract important info from a VCS requirement URL.

    Given a URL such as git+https://user:pass@host:port/namespace/repo.git@123456?foo=bar#egg=spam
    this function will extract:
    - the "clean" URL: https://user:pass@host:port/namespace/repo.git
    - the git ref: 123456
    - the host, namespace and repo: host:port, namespace, repo

    The clean URL and ref can be passed straight to scm.Git to fetch the repo.
    The host, namespace and repo will be used to construct the file path under deps/pip.

    :param str vcs_url: The URL of a VCS requirement, must be valid (have git ref in path)
    :return: Dict with url, ref, host, namespace and repo keys
    """
    # If scheme is git+protocol://, keep only protocol://
    # Do this before parsing URL, otherwise urllib may not extract URL params
    if vcs_url.startswith("git+"):
        vcs_url = vcs_url[len("git+") :]

    url = urllib.parse.urlparse(vcs_url)

    ref = url.path[-40:]  # Take the last 40 characters (the git ref)
    clean_path = url.path[:-41]  # Drop the last 41 characters ('@' + git ref)

    # Note: despite starting with an underscore, the namedtuple._replace() method is public
    clean_url = url._replace(path=clean_path, params="", query="", fragment="")

    # Assume everything up to the last '@' is user:pass. This should be kept in the
    # clean URL used for fetching, but should not be considered part of the host.
    _, _, clean_netloc = url.netloc.rpartition("@")

    namespace_repo = clean_path.strip("/")
    if namespace_repo.endswith(".git"):
        namespace_repo = namespace_repo[: -len(".git")]

    # Everything up to the last '/' is namespace, the rest is repo
    namespace, _, repo = namespace_repo.rpartition("/")

    return {
        "url": clean_url.geturl(),
        "ref": ref.lower(),
        "host": clean_netloc,
        "namespace": namespace,
        "repo": repo,
    }
