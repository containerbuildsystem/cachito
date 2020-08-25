# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import logging
import os
import time

import requests.auth

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config
from cachito.workers.errors import NexusScriptError


log = logging.getLogger(__name__)


def get_nexus_hoster_credentials():
    """
    Get the username and password of the account to use on Nexus instance that hosts content.

    :return: a tuple of username and password
    :rtype: tuple(str, str)
    """
    config = get_worker_config()

    if config.cachito_nexus_hoster_username:
        username = config.cachito_nexus_hoster_username
    else:
        username = config.cachito_nexus_username

    if config.cachito_nexus_hoster_password:
        password = config.cachito_nexus_hoster_password
    else:
        password = config.cachito_nexus_password

    return username, password


def _get_nexus_hoster_url():
    """
    Get the Nexus instance with the hosted repositories.

    :return: the URL to the Nexus instance
    :rtype: str
    """
    config = get_worker_config()

    if config.cachito_nexus_hoster_url:
        return config.cachito_nexus_hoster_url.rstrip("/")

    return config.cachito_nexus_url.rstrip("/")


def create_or_update_script(script_name, script_path):
    """
    Create or update a Nexus script to be executed by the REST API.

    :param str script_name: the name of the script
    :param str script_path: the path of the script
    :raise CachitoError: if the request fails
    """
    # Import this here to avoid a circular import
    from cachito.workers.requests import requests_session

    config = get_worker_config()
    auth = requests.auth.HTTPBasicAuth(config.cachito_nexus_username, config.cachito_nexus_password)

    def _request(http_method, url, error_msg, **kwargs):
        try:
            return requests_session.request(
                http_method, url, auth=auth, timeout=config.cachito_nexus_timeout, **kwargs
            )
        except requests.RequestException:
            log.exception(error_msg)
            raise CachitoError(error_msg)

    log.info("Checking if the script %s exists", script_name)
    script_base_url = f"{config.cachito_nexus_url.rstrip('/')}/service/rest/v1/script"
    script_url = f"{script_base_url}/{script_name}"
    error_msg = f"The connection failed when determining if the Nexus script {script_name} exists"
    rv_get = _request("get", script_url, error_msg)

    with open(script_path, "r") as f:
        script_content = f.read()

    payload = {"name": script_name, "type": "groovy", "content": script_content}

    if rv_get.status_code == 404:
        log.info("Creating the script %s", script_name)
        error_msg = f"The connection failed when creating the Nexus script {script_name}"
        rv_script = _request("post", script_base_url, error_msg, json=payload)
    elif rv_get.status_code == 200:
        if rv_get.json()["content"] == script_content:
            log.info("The script %s is already the latest", script_name)
            return

        log.info("Updating the script %s", script_name)
        error_msg = f"The connection failed when updating the Nexus script {script_name}"
        rv_script = _request("put", script_url, error_msg, json=payload)
    else:
        log.error(
            'Failed to determine if the Nexus script "%s" exists. The status was %d. '
            "The text was:\n%s",
            script_path,
            rv_get.status_code,
            rv_get.text,
        )
        raise CachitoError(f"Failed to determine if the Nexus script {script_name} exists")

    if not rv_script.ok:
        log.error(
            'Failed to create/update the Nexus script "%s". The status was %d. '
            "The text was:\n%s",
            script_path,
            rv_script.status_code,
            rv_script.text,
        )
        raise CachitoError(f"Failed to create/update the Nexus script {script_name}")


def create_or_update_scripts():
    """
    Create or update the Cachito Nexus scripts on the Nexus instance.

    This should be executed after Cachito is deployed or upgraded.

    :raise CachitoError: if the request fails
    """
    file_dir_path = os.path.dirname(os.path.abspath(__file__))
    script_dir_path = os.path.join(file_dir_path, "nexus_scripts")
    script_extension = ".groovy"
    for script in os.listdir(script_dir_path):
        if not script.endswith(script_extension):
            continue

        script_name = script[: -len(script_extension)]
        log.info("Creating or updating the Nexus script %s", script_name)
        script_path = os.path.join(script_dir_path, script)
        create_or_update_script(script_name, script_path)


def execute_script(script_name, payload):
    """
    Execute a script using the Nexus REST API.

    :param str script_name: the name of the script to execute
    :param dict payload: the JSON payload to send as arguments to the script
    :raise NexusScriptError: if the script execution fails
    """
    # Import this here to avoid a circular import
    from cachito.workers.requests import requests_session

    config = get_worker_config()
    auth = requests.auth.HTTPBasicAuth(config.cachito_nexus_username, config.cachito_nexus_password)
    script_url = f"{config.cachito_nexus_url.rstrip('/')}/service/rest/v1/script/{script_name}/run"

    log.info("Executing the Nexus script %s", script_name)
    try:
        rv = requests_session.post(
            script_url, auth=auth, json=payload, timeout=config.cachito_nexus_timeout
        )
    except requests.RequestException:
        error_msg = f"Could not connect to the Nexus instance to execute the script {script_name}"
        log.exception(error_msg)
        raise NexusScriptError(error_msg)

    if not rv.ok:
        log.error(
            "The Nexus script %s failed with the status code %d and the text: %s",
            script_name,
            rv.status_code,
            rv.text,
        )
        raise NexusScriptError(f"The Nexus script {script_name} failed with: {rv.text}")

    log.info("The Nexus script %s executed successfully", script_name)


def get_ca_cert():
    """
    Get the CA certificate that signed the Nexus instance's SSL certificate.

    :return: the string of the CA certificate or None
    :rtype: str or None
    """
    config = get_worker_config()

    if config.cachito_nexus_ca_cert and os.path.exists(config.cachito_nexus_ca_cert):
        with open(config.cachito_nexus_ca_cert, "r") as f:
            return f.read()


def get_component_info_from_nexus(
    repository, component_format, name, version=None, group=None, max_attempts=1
):
    """
    Get the component information from a Nexus repository using Nexus' REST API.

    Nexus accepts wildcards for some of the parameters in a search query (e.g. name, version).
    If you do use wildcards, make sure they will only match one component.

    :param str repository: the name of the repository
    :param str component_format: the format of the component (e.g. npm)
    :param str name: the name of the component, if format is raw then this is the unique identifier
    :param str version: the version of the dependency, should be specified if format is not raw
    :param str group: an optional group of the dependency (e.g. the scope of a npm package)
    :param int max_attempts: the number of attempts to try to get a result; this defaults to ``1``
    :return: the JSON about the component or None
    :rtype: dict or None
    :raise CachitoError: if the search fails or more than one component is returned
    """
    if max_attempts < 1:
        raise ValueError("The max_attempts parameter must be at least 1")

    if (version is None) != (component_format == "raw"):
        raise ValueError("'version' argument must be provided if and only if format is not 'raw'")

    component = None
    attempts = 0
    while component is None and attempts < max_attempts:
        if attempts != 0:
            log.warning(
                "The component search did not yield any results. Trying again in three seconds."
            )
            time.sleep(3)

        components = search_components(
            format=component_format, group=group, name=name, repository=repository, version=version
        )
        if len(components) > 1:
            log.error(
                "The following Nexus components were returned but more than one was not "
                "expected:\n%r",
                components,
            )
            raise CachitoError(
                "The component search in Nexus unexpectedly returned more than one result"
            )
        if components:
            return components[0]

        attempts += 1

    return None


def get_raw_component_asset_url(repository, name, max_attempts=1):
    """
    Get download URL for the asset of a raw component.

    In a raw repo, the name of a component identifies it uniquely. However, the name parameter
    of a search query does accept wildcards, so make sure only component will be found.

    :param str repository: the name of the repository
    :param str name: the name of the component (directory + filename)
    :param int max_attempts: the number of attempts to try to get a result; this defaults to ``1``
    :return: download URL for the asset, or None if component was not found
    """
    component = get_component_info_from_nexus(repository, "raw", name, max_attempts=max_attempts)
    if component is None:
        return None

    assets = component["assets"]

    # Sanity checks, in practice this should not happen
    if not assets:
        raise RuntimeError(f"Component {name} has no assets")
    if len(assets) > 1:
        log.debug("All assets: %r", assets)
        raise RuntimeError(f"Component {name} has more than 1 asset")

    return assets[0]["downloadUrl"]


def search_components(**query_params):
    """
    Search for components using the Nexus REST API.

    :param query_params: the query parameters to filter
    :return: the list of components returned by the search
    :rtype: list<dict>
    :raise CachitoError: if the search fails
    """
    # Import this here to avoid a circular import
    from cachito.workers.requests import requests_session

    username, password = get_nexus_hoster_credentials()
    auth = requests.auth.HTTPBasicAuth(username, password)
    url = f"{_get_nexus_hoster_url()}/service/rest/v1/search"
    # Create a copy so that the original query parameters are unaltered later on
    params = copy.deepcopy(query_params)
    config = get_worker_config()

    log.debug(
        "Searching Nexus for components using the following query parameters: %r", query_params
    )
    items = []
    while True:
        try:
            rv = requests_session.get(
                url, auth=auth, params=params, timeout=config.cachito_nexus_timeout
            )
        except requests.RequestException:
            msg = "Could not connect to the Nexus instance to search for components"
            log.exception(msg)
            raise CachitoError(msg)

        if not rv.ok:
            log.error(
                "Failed to search for components (%r) in Nexus with the status code %d and the "
                "text: %s",
                query_params,
                rv.status_code,
                rv.text,
            )
            raise CachitoError("Failed to search for components in Nexus")

        rv_json = rv.json()
        items.extend(rv_json["items"])

        # Handle pagination
        if rv_json["continuationToken"]:
            log.debug("Getting the next page of Nexus component search results")
            params["continuationToken"] = rv_json["continuationToken"]
        else:
            break

    return items


def upload_asset_only_component(repo_name, repo_type, component_path, to_nexus_hoster=True):
    """
    Upload an asset only component to a Nexus hosted repository.

    :param str repo_name: the name of the Nexus hosted repository
    :param str repo_type: the type of the Nexus hosted repository (e.g. ``npm``)
    :param str component_path: the path to the component to upload
    :param bool to_nexus_hoster: Use the nexus hoster instance, if available
    :raise CachitoError: if the upload fails
    :raise ValueError: if uploading to an unsupported or non-asset-only repository type
    """
    NEXUS_ASSET_ONLY_UPLOAD_TYPES = ("pypi", "npm", "nuget", "rubygems")
    if repo_type not in NEXUS_ASSET_ONLY_UPLOAD_TYPES:
        raise ValueError(f"Type {repo_type!r} is not supported or requires additional params")

    params = {"repository": repo_name}
    with open(component_path, "rb") as component:
        payload = {f"{repo_type}.asset": component.read()}

    log.info("Uploading the component %r to the %r Nexus repository", component_path, repo_type)
    try:
        upload_component(params, payload, to_nexus_hoster)
    except CachitoError:
        log.exception("Failed to upload %r to the %r Nexus repository", component_path, repo_type)
        raise


def upload_raw_component(repo_name, directory, components, to_nexus_hoster=True):
    """
    Upload a component to a Nexus raw repository.

    :param str repo_name: the name of the Nexus raw hosted repository
    :param str directory: destination to upload files to
    :param list components: a list of dicts with the "path" of the file to be uploaded and the
        "filename" to be saved in the destination directory.
    :param bool to_nexus_hoster: Use the nexus hoster instance, if available
    :raise CachitoError: if the upload fails
    """
    params = {"repository": repo_name}
    payload = {"raw.directory": directory}
    for index, component in enumerate(components):
        n = index + 1
        payload[f"raw.asset{n}.filename"] = component["filename"]
        with open(component["path"], "rb") as f:
            payload[f"raw.asset{n}"] = f.read()

    try:
        upload_component(params, payload, to_nexus_hoster)
    except CachitoError:
        log.exception("Failed to upload %r to the raw Nexus repository", components)
        raise


def upload_component(params, payload, to_nexus_hoster):
    """
    Push a payload to the Nexus upload endpoint.

    See https://help.sonatype.com/repomanager3/rest-and-integration-api/components-api for further
    reference.

    :param dict params: the request parameters to the upload endpoint (e.g. {"repository": NAME})
    :param dict payload: Nexus API compliant file payload
    :param bool to_nexus_hoster: Use the nexus hoster instance, if available
    :raise CachitoError: if the upload fails
    """
    # Import this here to avoid a circular import
    from cachito.workers.requests import requests_session

    config = get_worker_config()
    if to_nexus_hoster:
        username, password = get_nexus_hoster_credentials()
        nexus_url = _get_nexus_hoster_url()
    else:
        username = config.cachito_nexus_username
        password = config.cachito_nexus_password
        nexus_url = config.cachito_nexus_url

    auth = requests.auth.HTTPBasicAuth(username, password)
    endpoint = f"{nexus_url}/service/rest/v1/components"

    try:
        rv = requests_session.post(
            endpoint, auth=auth, files=payload, params=params, timeout=config.cachito_nexus_timeout
        )
    except requests.RequestException:
        log.exception("Could not connect to the Nexus instance to upload the component")
        raise CachitoError("Could not connect to the Nexus instance to upload a component")

    if not rv.ok:
        log.error(
            "Failed to upload a component with the status code %d and the text: %s",
            rv.status_code,
            rv.text,
        )
        raise CachitoError("Failed to upload a component to Nexus")
