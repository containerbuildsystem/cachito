# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import json
import logging
import os

from cachito.errors import CachitoError
from cachito.workers import nexus
from cachito.workers.config import validate_nexus_config
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general import (
    update_request_with_config_files,
    update_request_with_deps,
    update_request_with_packages,
)
from cachito.workers.pkg_managers.general_js import (
    generate_npmrc_content,
    get_js_proxy_repo_name,
    get_js_proxy_username,
    finalize_nexus_for_js_request,
    prepare_nexus_for_js_request,
)
from cachito.workers.pkg_managers.npm import resolve_npm
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.general import set_request_state


__all__ = ["cleanup_npm_request", "fetch_npm_source"]
log = logging.getLogger(__name__)


@app.task
def cleanup_npm_request(request_id):
    """Clean up the Nexus npm content for the Cachito request."""
    payload = {
        "repository_name": get_js_proxy_repo_name(request_id),
        "username": get_js_proxy_username(request_id),
    }
    nexus.execute_script("js_cleanup", payload)


@app.task
def fetch_npm_source(request_id, auto_detect=False):
    """
    Resolve and fetch npm dependencies for a given request.

    :param int request_id: the Cachito request ID this is for
    :param bool auto_detect: automatically detect if the archive uses npm
    :raise CachitoError: if the task fails
    """
    validate_nexus_config()

    bundle_dir = RequestBundleDir(request_id)
    log.debug("Checking if the application source uses npm")
    for lock_file in (bundle_dir.npm_shrinkwrap_file, bundle_dir.npm_package_lock_file):
        if lock_file.exists():
            break
    else:
        if auto_detect:
            log.info("The application source does not use npm")
            return

        raise CachitoError(
            "The npm-shrinkwrap.json or package-lock.json file must be present for the npm "
            "package manager"
        )

    log.debug("Ensuring there is no node_modules directory present")
    if bundle_dir.npm_deps_dir.joinpath("node_modules").exists():
        raise CachitoError("The node_modules directory cannot be present in the source repository")

    log.debug("Ensuring that the package.json file is present")
    if not bundle_dir.npm_package_file.exists():
        raise CachitoError("The package.json file is not present in the source repository")

    log.info("Configuring Nexus for npm for the request %d", request_id)
    set_request_state(request_id, "in_progress", "Configuring Nexus for npm")
    prepare_nexus_for_js_request(request_id)

    log.info("Fetching the npm dependencies for request %d", request_id)
    request = set_request_state(request_id, "in_progress", "Fetching the npm dependencies")
    try:
        package_and_deps_info = resolve_npm(str(bundle_dir.source_dir), request)
    except CachitoError:
        log.exception("Failed to fetch npm dependencies for request %d", request_id)
        raise

    log.info("Finalizing the Nexus configuration for npm for the request %d", request_id)
    set_request_state(request_id, "in_progress", "Finalizing the Nexus configuration for npm")
    username, password = finalize_nexus_for_js_request(request_id)

    log.info("Generating the .npmrc file")
    ca_cert = nexus.get_ca_cert()
    if ca_cert:
        # The custom CA will be called registry-ca.pem in the same directory as the .npmrc file
        npm_config_files = [
            {
                "content": base64.b64encode(ca_cert.encode("utf-8")).decode("utf-8"),
                "path": "app/registry-ca.pem",
                "type": "base64",
            }
        ]
        custom_ca_path = "./registry-ca.pem"
    else:
        npm_config_files = []
        custom_ca_path = None

    npm_rc = generate_npmrc_content(request_id, username, password, custom_ca_path=custom_ca_path)
    npm_config_files.append(
        {
            "content": base64.b64encode(npm_rc.encode("utf-8")).decode("utf-8"),
            "path": "app/.npmrc",
            "type": "base64",
        }
    )

    if package_and_deps_info["package.json"]:
        package_json_str = json.dumps(package_and_deps_info["package.json"], indent=2)
        npm_config_files.append(
            {
                "content": base64.b64encode(package_json_str.encode("utf-8")).decode("utf-8"),
                "path": "app/package.json",
                "type": "base64",
            }
        )

    if package_and_deps_info["lock_file"]:
        package_lock_str = json.dumps(package_and_deps_info["lock_file"], indent=2)
        npm_config_files.append(
            {
                "content": base64.b64encode(package_lock_str.encode("utf-8")).decode("utf-8"),
                "path": f"app/{os.path.basename(str(lock_file))}",
                "type": "base64",
            }
        )

    update_request_with_config_files(request_id, npm_config_files)

    update_request_with_packages(request_id, [package_and_deps_info["package"]], "npm")
    update_request_with_deps(request_id, package_and_deps_info["deps"])
