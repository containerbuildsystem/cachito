# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import logging
import os

from cachito.errors import CachitoError
from cachito.utils import PackagesData
from cachito.workers import nexus
from cachito.workers.config import get_worker_config, validate_pip_config
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general import (
    update_request_with_config_files,
    update_request_with_deps,
    update_request_with_package,
)
from cachito.workers.pkg_managers.pip import (
    PipRequirementsFile,
    finalize_nexus_for_pip_request,
    get_index_url,
    get_pypi_hosted_repo_name,
    get_pypi_hosted_repo_url,
    get_raw_component_name,
    get_raw_hosted_repo_name,
    get_hosted_repositories_username,
    prepare_nexus_for_pip_request,
    resolve_pip,
)
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.utils import (
    make_base64_config_file,
    runs_if_request_in_progress,
    get_request,
    set_request_state,
)


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
@runs_if_request_in_progress
def fetch_pip_source(request_id, package_configs=None):
    """
    Resolve and fetch pip dependencies for a given request.

    :param int request_id: the Cachito request ID this is for
    :param list package_configs: the list of optional package configurations submitted by the user
    """
    validate_pip_config()
    bundle_dir: RequestBundleDir = RequestBundleDir(request_id)

    log.info("Configuring Nexus for pip for the request %d", request_id)
    set_request_state(request_id, "in_progress", "Configuring Nexus for pip")
    pip_repo_name = get_pypi_hosted_repo_name(request_id)
    raw_repo_name = get_raw_hosted_repo_name(request_id)
    prepare_nexus_for_pip_request(pip_repo_name, raw_repo_name)

    log.info("Fetching dependencies for request %d", request_id)
    package_configs = package_configs or [{}]
    packages_data = []
    requirement_file_paths = []
    for pkg_cfg in package_configs:
        pkg_path = pkg_cfg.get("path", ".")
        source_dir = bundle_dir.app_subpath(pkg_path).source_dir
        set_request_state(
            request_id, "in_progress", f"Fetching dependencies at the {pkg_path!r} directory",
        )
        request = get_request(request_id)
        pkg_and_deps_info = resolve_pip(
            source_dir,
            request,
            requirement_files=pkg_cfg.get("requirements_files"),
            build_requirement_files=pkg_cfg.get("requirements_build_files"),
        )

        # defer custom requirement files creation to use the Nexus password in the URLs
        for requirement_file_path in pkg_and_deps_info.pop("requirements"):
            requirement_file_paths.append(requirement_file_path)

        # defer DB operations to use the Nexus password in the env vars
        packages_data.append(pkg_and_deps_info)

    log.info("Finalizing the Nexus configuration for pip for the request %d", request_id)
    set_request_state(request_id, "in_progress", "Finalizing the Nexus configuration for pip")
    username = get_hosted_repositories_username(request_id)
    password = finalize_nexus_for_pip_request(pip_repo_name, raw_repo_name, username)

    # Set environment variables and config files
    pip_config_files = []
    for requirement_file_path in requirement_file_paths:
        custom_requirement_file = _get_custom_requirement_config_file(
            requirement_file_path, bundle_dir.source_root_dir, raw_repo_name, username, password
        )
        if custom_requirement_file:
            pip_config_files.append(custom_requirement_file)

    raw_url = get_pypi_hosted_repo_url(request_id)
    pip_index_url = get_index_url(raw_url, username, password)
    env_vars = {"PIP_INDEX_URL": {"value": pip_index_url, "kind": "literal"}}
    ca_cert = nexus.get_ca_cert()
    if ca_cert:
        ca_cert_path = os.path.join("app", "package-index-ca.pem")
        env_vars["PIP_CERT"] = {"value": ca_cert_path, "kind": "path"}
        pip_config_files.append(make_base64_config_file(ca_cert, ca_cert_path))

    worker_config = get_worker_config()
    env_vars.update(worker_config.cachito_default_environment_variables.get("pip", {}))

    packages_json_data = PackagesData()

    # Finally, perform DB operations
    for pkg_cfg, pkg_data in zip(package_configs, packages_data):
        pkg_subpath = os.path.normpath(pkg_cfg.get("path", "."))
        pkg_info = pkg_data["package"]
        pkg_deps = pkg_data["dependencies"]

        update_request_with_package(request_id, pkg_info, env_vars, package_subpath=pkg_subpath)
        update_request_with_deps(request_id, pkg_info, pkg_deps)
        packages_json_data.add_package(pkg_info, pkg_subpath, pkg_deps)

    packages_json_data.write_to_file(bundle_dir.pip_packages_data)

    if pip_config_files:
        update_request_with_config_files(request_id, pip_config_files)


def _get_custom_requirement_config_file(
    requirement_file_path, source_dir, raw_repo_name, username, password
):
    """
    Get custom pip requirement file.

    Generates and returns a configuration file representing a custom pip requirement file where the
    original URL and VCS entries are replaced with entries pointing to those resources in Nexus.

    :param str requirement_file_path: path to the requirement file
    :param Path source_dir: path to the application source code
    :param str raw_repo_name: name of the raw hosted Nexus repository containing the
        requirements
    :param str username: the username of the Nexus user that has access to the request's Python
        repositories
    :param str password: the password of the Nexus user that has access to the request's Python
        repositories
    :return: Cachito configuration file representation containing the custom requirement file
    :rtype: dict
    :raises CachitoError: If a valid component URL cannot be retrieved from the raw Nexus repository
    """
    original_requirement_file = PipRequirementsFile(requirement_file_path)
    cachito_requirements = []
    differs_from_original = False
    for requirement in original_requirement_file.requirements:
        raw_component_name = get_raw_component_name(requirement)
        if raw_component_name:
            # increase max_attempts to make sure the package upload/setup is complete
            worker_config = get_worker_config()
            max_attempts = worker_config.cachito_nexus_max_search_attempts
            new_url = nexus.get_raw_component_asset_url(
                raw_repo_name,
                raw_component_name,
                max_attempts=max_attempts,
                from_nexus_hoster=False,
            )
            if not new_url:
                raise CachitoError(
                    f"Could not retrieve URL for {raw_component_name} in {raw_repo_name}. Was the "
                    "asset uploaded?"
                )

            # Inject credentials
            if "://" not in new_url:
                raise CachitoError(f"Nexus raw resource URL: {new_url} is not a valid URL")

            new_url = new_url.replace("://", f"://{username}:{password}@", 1)
            requirement = requirement.copy(url=new_url)
            differs_from_original = True

        cachito_requirements.append(requirement)

    if not differs_from_original:
        # No vcs or url dependencies. No need for a custom requirements file
        return

    cachito_requirement_file = PipRequirementsFile.from_requirements_and_options(
        cachito_requirements, original_requirement_file.options
    )
    final_contents = []
    if cachito_requirement_file.options:
        final_contents.append(" ".join(cachito_requirement_file.options))

    final_contents.extend(
        [str(requirement) for requirement in cachito_requirement_file.requirements]
    )
    req_str = "\n".join(final_contents)
    final_path = Path("app") / Path(requirement_file_path).relative_to(source_dir)
    return make_base64_config_file(req_str, final_path)
