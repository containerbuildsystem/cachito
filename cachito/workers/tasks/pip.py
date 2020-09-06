# SPDX-License-Identifier: GPL-3.0-or-later
import logging

from cachito.workers import nexus
from cachito.workers.config import get_worker_config, validate_pip_config
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general import (
    update_request_with_deps,
    update_request_with_package,
)
from cachito.workers.pkg_managers.pip import (
    finalize_nexus_for_pip_request,
    get_index_url,
    get_pypi_hosted_repo_name,
    get_pypi_hosted_repo_url,
    get_raw_hosted_repo_name,
    get_hosted_repositories_username,
    prepare_nexus_for_pip_request,
    resolve_pip,
)
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.general import set_request_state


log = logging.getLogger(__name__)
__all__ = ["cleanup_pip_request", "fetch_pip_source"]


@app.task
def cleanup_pip_request(request_id):
    """Clean up the Nexus Python content for the Cachito request."""
    payload = {
        "pip_repository_name": get_pypi_hosted_repo_name(request_id),
        "raw_repository_name": get_raw_hosted_repo_name(request_id),
        "username": get_hosted_repositories_username(request_id),
    }
    nexus.execute_script("pip_cleanup", payload)


@app.task
def fetch_pip_source(request_id, package_configs=None):
    """
    Resolve and fetch pip dependencies for a given request.

    :param int request_id: the Cachito request ID this is for
    :param list package_configs: the list of optional package configurations submitted by the user
    """
    validate_pip_config()
    bundle_dir = RequestBundleDir(request_id)

    log.info("Configuring Nexus for pip for the request %d", request_id)
    set_request_state(request_id, "in_progress", "Configuring Nexus for pip")
    pip_repo_name = get_pypi_hosted_repo_name(request_id)
    raw_repo_name = get_raw_hosted_repo_name(request_id)
    prepare_nexus_for_pip_request(pip_repo_name, raw_repo_name)

    log.info("Fetching dependencies for request %d", request_id)
    package_configs = package_configs or [{}]
    packages_data = []
    for pkg_cfg in package_configs:
        pkg_path = pkg_cfg.get("path", ".")
        source_dir = bundle_dir.app_subpath(pkg_path).source_dir
        request = set_request_state(
            request_id, "in_progress", f"Fetching dependencies at the {pkg_path!r} directory",
        )
        pkg_and_deps_info = resolve_pip(
            source_dir,
            request,
            requirement_files=pkg_cfg.get("requirements_files"),
            build_requirement_files=pkg_cfg.get("requirements_build_files"),
        )

        # defer DB operations to use the Nexus password in the env vars
        packages_data.append(pkg_and_deps_info)

    log.info("Finalizing the Nexus configuration for pip for the request %d", request_id)
    set_request_state(request_id, "in_progress", "Finalizing the Nexus configuration for pip")
    username = get_hosted_repositories_username(request_id)
    password = finalize_nexus_for_pip_request(pip_repo_name, raw_repo_name, username)

    # Set environment variables
    raw_url = get_pypi_hosted_repo_url(request_id)
    pip_index_url = get_index_url(raw_url, username, password)
    env_vars = {"PIP_INDEX_URL": {"value": pip_index_url, "kind": "literal"}}
    ca_cert = nexus.get_ca_cert()
    if ca_cert:
        env_vars["PIP_CERT"] = {"value": ca_cert, "kind": "literal"}

    worker_config = get_worker_config()
    env_vars.update(worker_config.cachito_default_environment_variables.get("pip", {}))

    # Finally, perform DB operations
    for pkg_data in packages_data:
        update_request_with_package(request_id, pkg_data["package"], env_vars)
        update_request_with_deps(request_id, pkg_data["package"], pkg_data["dependencies"])
