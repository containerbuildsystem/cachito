# SPDX-License-Identifier: GPL-3.0-or-later
import json
import logging
import os
from typing import List

from cachito.errors import CachitoError
from cachito.workers import nexus
from cachito.workers.config import get_worker_config, validate_npm_config
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general import (
    update_request_with_config_files,
    update_request_with_deps,
    update_request_with_package,
)
from cachito.workers.pkg_managers.general_js import (
    generate_npmrc_content,
    finalize_nexus_for_js_request,
    prepare_nexus_for_js_request,
)
from cachito.workers.pkg_managers.npm import (
    get_npm_proxy_repo_name,
    get_npm_proxy_repo_url,
    get_npm_proxy_username,
    resolve_npm,
)
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.general import set_request_state
from cachito.workers.tasks.utils import (
    make_base64_config_file,
    runs_if_request_in_progress,
    get_request,
)

__all__ = ["cleanup_npm_request", "fetch_npm_source"]
log = logging.getLogger(__name__)


def _verify_npm_files(bundle_dir, subpaths):
    """
    Verify that the expected npm files are present for the npm package manager to proceed.

    :param RequestBundleDir bundle_dir: the ``RequestBundleDir`` object for the request
    :param list subpaths: a list of subpaths in the source repository of npm packages
    :raises CachitoError: if the repository is missing the required files or contains invalid
        files/directories
    """
    for subpath in subpaths:
        bundle_dir_subpath = bundle_dir.app_subpath(subpath)
        lock_files = (
            bundle_dir_subpath.npm_shrinkwrap_file,
            bundle_dir_subpath.npm_package_lock_file,
        )
        for lock_file in lock_files:
            if lock_file.exists():
                break
        else:
            lock_files_relpath = tuple(
                bundle_dir_subpath.relpath(lock_file) for lock_file in lock_files
            )
            raise CachitoError(
                f"The {' or '.join(lock_files_relpath)} file must be present for the npm package "
                "manager"
            )

        if not bundle_dir_subpath.npm_package_file.exists():
            package_json_rel_path = bundle_dir_subpath.relpath(bundle_dir_subpath.npm_package_file)
            raise CachitoError(
                f"The {package_json_rel_path} file must be present for the npm package manager"
            )

        log.debug("Ensuring there is no node_modules directory present")
        if bundle_dir_subpath.node_modules.exists():
            node_modules_rel_path = bundle_dir_subpath.relpath(bundle_dir_subpath.node_modules)
            raise CachitoError(
                f"The {node_modules_rel_path} directory cannot be present in the source repository"
            )


@app.task
def cleanup_npm_request(request_id):
    """Clean up the Nexus npm content for the Cachito request."""
    payload = {
        "repository_name": get_npm_proxy_repo_name(request_id),
        "username": get_npm_proxy_username(request_id),
    }
    nexus.execute_script("js_cleanup", payload)


@app.task
@runs_if_request_in_progress
def fetch_npm_source(request_id, package_configs=None):
    """
    Resolve and fetch npm dependencies for a given request.

    This function uses the Python ``os.path`` library to manipulate paths, so the path to the
    configuration files may differ in format based on the system the Cachito worker is deployed on
    (i.e. Linux vs Windows).

    :param int request_id: the Cachito request ID this is for
    :param list package_configs: the list of optional package configurations submitted by the user
    :raise CachitoError: if the task fails
    """
    if package_configs is None:
        package_configs = []

    validate_npm_config()

    bundle_dir = RequestBundleDir(request_id)
    log.debug("Checking if the application source uses npm")
    subpaths = [os.path.normpath(c["path"]) for c in package_configs if c.get("path")]

    if not subpaths:
        # Default to the root of the application source
        subpaths = [os.curdir]

    _verify_npm_files(bundle_dir, subpaths)

    log.info("Configuring Nexus for npm for the request %d", request_id)
    set_request_state(request_id, "in_progress", "Configuring Nexus for npm")
    repo_name = get_npm_proxy_repo_name(request_id)
    prepare_nexus_for_js_request(repo_name)

    npm_config_files = []
    downloaded_deps = set()
    for i, subpath in enumerate(subpaths):
        log.info("Fetching the npm dependencies for request %d in subpath %s", request_id, subpath)
        set_request_state(
            request_id,
            "in_progress",
            f'Fetching the npm dependencies at the "{subpath}" directory"',
        )
        request = get_request(request_id)
        package_source_path = str(bundle_dir.app_subpath(subpath).source_dir)
        try:
            package_and_deps_info = resolve_npm(
                package_source_path, request, skip_deps=downloaded_deps
            )
        except CachitoError:
            log.exception("Failed to fetch npm dependencies for request %d", request_id)
            raise

        downloaded_deps = downloaded_deps | package_and_deps_info["downloaded_deps"]

        log.info(
            "Generating the npm configuration files for request %d in subpath %s",
            request_id,
            subpath,
        )
        remote_package_source_path = os.path.normpath(os.path.join("app", subpath))
        if package_and_deps_info["package.json"]:
            package_json_str = json.dumps(package_and_deps_info["package.json"], indent=2)
            package_json_path = os.path.join(remote_package_source_path, "package.json")
            npm_config_files.append(make_base64_config_file(package_json_str, package_json_path))

        if package_and_deps_info["lock_file"]:
            package_lock_str = json.dumps(package_and_deps_info["lock_file"], indent=2)
            lock_file_name = package_and_deps_info["lock_file_name"]
            lock_file_path = os.path.join(remote_package_source_path, lock_file_name)
            npm_config_files.append(make_base64_config_file(package_lock_str, lock_file_path))

        if i == 0:
            env_vars = get_worker_config().cachito_default_environment_variables.get("npm", {})
        else:
            env_vars = None
        package = package_and_deps_info["package"]
        update_request_with_package(request_id, package, env_vars, package_subpath=subpath)
        update_request_with_deps(request_id, package, package_and_deps_info["deps"])

    log.info("Finalizing the Nexus configuration for npm for the request %d", request_id)
    set_request_state(request_id, "in_progress", "Finalizing the Nexus configuration for npm")
    username = get_npm_proxy_username(request_id)
    password = finalize_nexus_for_js_request(username, repo_name)

    log.info("Generating the .npmrc file(s)")
    proxy_repo_url = get_npm_proxy_repo_url(request_id)
    npm_config_files.extend(
        generate_npmrc_config_files(proxy_repo_url, username, password, subpaths)
    )

    update_request_with_config_files(request_id, npm_config_files)


def generate_npmrc_config_files(
    proxy_repo_url: str, username: str, password: str, subpaths: List[str],
) -> List[dict]:
    """
    Generate one .npmrc config file for each subpath in request.

    If Nexus has a CA cert, it will also be added as a configuration file.

    The contents of all .nmprc files are the same except for the 'cafile' option, which defines
    the relative path from the app directory to the CA cert.

    :param str proxy_repo_url: url of the npm proxy repo
    :param str username: username with read access to the proxy repo
    :param str password: the password for the corresponding username
    :param list[str] subpaths: list of package subpaths in request
    :return: list of config files to be added to the request
    """
    config_files = []

    ca_cert = nexus.get_ca_cert()
    if ca_cert:
        # The custom CA will be called registry-ca.pem in the "app" directory
        ca_path = os.path.join("app", "registry-ca.pem")
        config_files.append(make_base64_config_file(ca_cert, ca_path))

    for subpath in subpaths:
        if ca_cert:
            # Determine the relative path to the registry-ca.pem file
            custom_ca_path = os.path.relpath("registry-ca.pem", start=subpath)
        else:
            custom_ca_path = None

        npm_rc = generate_npmrc_content(
            proxy_repo_url, username, password, custom_ca_path=custom_ca_path
        )
        npm_rc_path = os.path.normpath(os.path.join("app", subpath, ".npmrc"))
        config_files.append(make_base64_config_file(npm_rc, npm_rc_path))

    return config_files
