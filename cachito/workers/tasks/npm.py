# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import json
import logging
import os

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
        request = set_request_state(
            request_id,
            "in_progress",
            f'Fetching the npm dependencies at the "{subpath}" directory"',
        )
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
            npm_config_files.append(
                {
                    "content": base64.b64encode(package_json_str.encode("utf-8")).decode("utf-8"),
                    "path": os.path.join(remote_package_source_path, "package.json"),
                    "type": "base64",
                }
            )

        if package_and_deps_info["lock_file"]:
            package_lock_str = json.dumps(package_and_deps_info["lock_file"], indent=2)
            lock_file_name = package_and_deps_info["lock_file_name"]
            npm_config_files.append(
                {
                    "content": base64.b64encode(package_lock_str.encode("utf-8")).decode("utf-8"),
                    "path": os.path.join(remote_package_source_path, lock_file_name),
                    "type": "base64",
                }
            )

        if i == 0:
            env_vars = get_worker_config().cachito_default_environment_variables.get("npm", {})
        else:
            env_vars = None
        package = package_and_deps_info["package"]
        update_request_with_package(request_id, package, env_vars)
        update_request_with_deps(request_id, package, package_and_deps_info["deps"])

    log.info("Finalizing the Nexus configuration for npm for the request %d", request_id)
    set_request_state(request_id, "in_progress", "Finalizing the Nexus configuration for npm")
    username = get_npm_proxy_username(request_id)
    password = finalize_nexus_for_js_request(username, repo_name)

    log.info("Generating the .npmrc file(s)")
    ca_cert = nexus.get_ca_cert()
    if ca_cert:
        # The custom CA will be called registry-ca.pem in the "app" directory
        npm_config_files.append(
            {
                "content": base64.b64encode(ca_cert.encode("utf-8")).decode("utf-8"),
                "path": os.path.join("app", "registry-ca.pem"),
                "type": "base64",
            }
        )

    for subpath in subpaths:
        proxy_repo_url = get_npm_proxy_repo_url(request_id)
        if ca_cert:
            # Determine the relative path to the registry-ca.pem file
            custom_ca_path = os.path.relpath("registry-ca.pem", start=subpath)
        else:
            custom_ca_path = None
        npm_rc = generate_npmrc_content(
            proxy_repo_url, username, password, custom_ca_path=custom_ca_path
        )
        npm_config_files.append(
            {
                "content": base64.b64encode(npm_rc.encode("utf-8")).decode("utf-8"),
                "path": os.path.normpath(os.path.join("app", subpath, ".npmrc")),
                "type": "base64",
            }
        )

    update_request_with_config_files(request_id, npm_config_files)
