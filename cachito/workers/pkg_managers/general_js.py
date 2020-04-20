# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import logging
import os
import random
import secrets
import tempfile
import textwrap

from cachito.errors import CachitoError
from cachito.workers import nexus
from cachito.workers.config import get_worker_config
from cachito.workers.errors import NexusScriptError
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general import run_cmd

__all__ = [
    "download_dependencies",
    "finalize_nexus_for_js_request",
    "generate_and_write_npmrc_file",
    "generate_npmrc_content",
    "get_js_proxy_repo_name",
    "get_js_proxy_repo_url",
    "get_js_proxy_username",
    "prepare_nexus_for_js_request",
]

log = logging.getLogger(__name__)


def download_dependencies(request_id, deps):
    """
    Download the list of npm dependencies using npm pack to the deps bundle directory.

    By downloading the dependencies, this stages the content in the request specific npm proxy.

    Any dependency that has the key "bundled" set to ``True`` will not be downloaded. This is
    because the dependency is bundled as part of another dependency, and thus already present in
    the tarball of the dependency that bundles it.

    :param int request_id: the ID of the request these dependencies are being downloaded for
    :param list deps: a list of dependencies where each dependency has the keys: bundled, name,
        and version
    :raises CachitoError: if any of the downloads fail
    """
    conf = get_worker_config()
    with tempfile.TemporaryDirectory(prefix="cachito-") as temp_dir:
        npm_rc_file = os.path.join(temp_dir, ".npmrc")
        # The token must be privileged so that it has access to the cachito-js repository
        generate_and_write_npmrc_file(
            npm_rc_file, request_id, conf.cachito_nexus_username, conf.cachito_nexus_password
        )
        env = {
            "NPM_CONFIG_CACHE": os.path.join(temp_dir, "cache"),
            "NPM_CONFIG_USERCONFIG": npm_rc_file,
            "PATH": os.environ.get("PATH", ""),
        }
        bundle_dir = RequestBundleDir(request_id)
        bundle_dir.npm_deps_dir.mkdir(exist_ok=True)
        # Download the dependencies directly in the deps/npm bundle directory
        run_params = {"env": env, "cwd": str(bundle_dir.npm_deps_dir)}

        log.info("Processing %d npm dependencies to stage in Nexus", len(deps))
        # This must be done in batches to prevent Nexus from erroring with "Header is too large"
        deps_batches = []
        counter = 0
        batch_size = get_worker_config().cachito_js_download_batch_size
        for dep in deps:
            dep_identifier = f"{dep['name']}@{dep['version']}"
            if dep["bundled"]:
                log.debug("Not downloading %s since it is a bundled dependency", dep_identifier)
                continue

            if counter % batch_size == 0:
                deps_batches.append([])
            deps_batches[-1].append(dep_identifier)
            counter += 1

        for dep_batch in deps_batches:
            log.debug(f"Downloading the following npm dependencies: {', '.join(dep_batch)}")
            npm_pack_args = ["npm", "pack"] + dep_batch
            run_cmd(npm_pack_args, run_params, "Failed to download the npm dependencies")


def finalize_nexus_for_js_request(request_id):
    """
    Finalize the Nexus configuration so that the request's npm repository is ready for consumption.

    :param int request_id: the ID of the request that Nexus should be configured for
    :return: the username and password of the Nexus user that has access to the request's npm
        repository
    :rtype: (str, str)
    :raise CachitoError: if the script execution fails
    """
    username = get_js_proxy_username(request_id)
    # Generate a 24-32 character (each byte is two hex characters) password
    password = secrets.token_hex(random.randint(12, 16))
    payload = {
        "password": password,
        "repository_name": get_js_proxy_repo_name(request_id),
        "username": username,
    }
    script_name = "js_after_content_staged"
    try:
        nexus.execute_script(script_name, payload)
    except NexusScriptError:
        log.exception("Failed to execute the script %s", script_name)
        raise CachitoError(
            "Failed to configure Nexus to allow the request's npm repository to be ready for "
            "consumption"
        )
    return username, password


def generate_and_write_npmrc_file(npm_rc_path, request_id, username, password, custom_ca_path=None):
    """
    Generate a .npmrc file at the input location with the registry and authentication configured.

    :param str npm_rc_path: the path to create the .npmrc file
    :param int request_id: the ID of the request to determine the npm registry URL
    :param str username: the username of the user to use for authenticating to the registry
    :param str password: the password of the user to use for authenticating to the registry
    :param str custom_ca_path: the path to set ``cafile`` to in the .npm rc file; if not provided,
        this option will be omitted
    """
    log.debug("Generating a .npmrc file at %s", npm_rc_path)
    with open(npm_rc_path, "w") as f:
        f.write(
            generate_npmrc_content(request_id, username, password, custom_ca_path=custom_ca_path)
        )


def generate_npmrc_content(request_id, username, password, custom_ca_path=None):
    """
    Generate a .npmrc file with the registry and authentication configured.

    :param int request_id: the ID of the request to determine the npm registry URL
    :param str username: the username of the user to use for authenticating to the registry
    :param str password: the password of the user to use for authenticating to the registry
    :param str custom_ca_path: the path to set ``cafile`` to in the .npm rc file; if not provided,
        this option will be omitted
    :return: the contents of the .npmrc file
    :rtype: str
    """
    proxy_repo_url = get_js_proxy_repo_url(request_id)
    # Instead of getting the token from Nexus, use basic authentication as supported by Nexus:
    # https://help.sonatype.com/repomanager3/formats/npm-registry#npmRegistry-AuthenticationUsingBasicAuth
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("utf-8")
    npm_rc = textwrap.dedent(
        f"""\
        registry={proxy_repo_url}
        email=noreply@domain.local
        always-auth=true
        _auth={token}
        fetch-retries=5
        fetch-retry-factor=2
        strict-ssl=true
        """
    )

    if custom_ca_path:
        # The CA could be embedded in the actual .npmrc file with the `ca` option, but this would
        # mean that the CA file contents would be duplicated in the Cachito database for every
        # request that uses a .npmrc file
        npm_rc += f'cafile="{custom_ca_path}"\n'

    return npm_rc


def get_js_proxy_repo_name(request_id):
    """
    Get the name of npm proxy repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the name of npm proxy repository for the request
    :rtype: str
    """
    return f"cachito-js-{request_id}"


def get_js_proxy_repo_url(request_id):
    """
    Get the URL for the Nexus npm proxy repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the URL for the Nexus npm proxy repository for the request
    :rtype: str
    """
    config = get_worker_config()
    repo_name = get_js_proxy_repo_name(request_id)
    return f"{config.cachito_nexus_url.rstrip('/')}/repository/{repo_name}/"


def get_js_proxy_username(request_id):
    """
    Get the username that has read access on the npm proxy repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the username
    :rtype: str
    """
    return f"cachito-js-{request_id}"


def prepare_nexus_for_js_request(request_id):
    """
    Prepare Nexus so that Cachito can stage JavaScript content.

    :param int request_id: the ID of the request that Nexus should be configured for
    :raise CachitoError: if the script execution fails
    """
    config = get_worker_config()
    # Note that the http_username and http_password represent the unprivileged user that
    # the new Nexus npm proxy repository will use to connect to the "cachito-js" Nexus group
    # repository
    payload = {
        "repository_name": get_js_proxy_repo_name(request_id),
        "http_password": config.cachito_nexus_unprivileged_password,
        "http_username": config.cachito_nexus_unprivileged_username,
    }
    script_name = "js_before_content_staged"
    try:
        nexus.execute_script(script_name, payload)
    except NexusScriptError:
        log.exception(f"Failed to execute the script {script_name}")
        raise CachitoError("Failed to prepare Nexus for Cachito to stage JavaScript content")
