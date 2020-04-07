# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os

import requests.auth

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config
from cachito.workers.errors import NexusScriptError


log = logging.getLogger(__name__)


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
