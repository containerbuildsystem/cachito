# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
from os.path import relpath
from pathlib import Path
from textwrap import dedent

from cachito.common.packages_data import PackagesData
from cachito.errors import CachitoError
from cachito.workers import nexus
from cachito.workers.config import validate_rubygems_config
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general import update_request_with_config_files
from cachito.workers.pkg_managers.rubygems import (
    finalize_nexus_for_rubygems_request,
    get_rubygems_hosted_repo_name,
    get_rubygems_hosted_url_with_credentials,
    get_rubygems_nexus_username,
    prepare_nexus_for_rubygems_request,
    resolve_rubygems,
)
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.utils import (
    get_request,
    make_base64_config_file,
    runs_if_request_in_progress,
    set_request_state,
)

__all__ = ["cleanup_rubygems_request", "fetch_rubygems_source"]
log = logging.getLogger(__name__)


@app.task
def cleanup_rubygems_request(request_id):
    """Clean up the Nexus RubyGems content for the Cachito request."""
    payload = {
        "rubygems_repository_name": get_rubygems_hosted_repo_name(request_id),
        "username": get_rubygems_nexus_username(request_id),
    }
    nexus.execute_script("rubygems_cleanup", payload)


@app.task
@runs_if_request_in_progress
def fetch_rubygems_source(request_id: int, package_configs: list[dict] = None):
    """
    Resolve and fetch RubyGems dependencies for a given request.

    :param int request_id: the Cachito request ID this is for
    :param list package_configs: the list of optional package configurations submitted by the user
    """
    validate_rubygems_config()
    bundle_dir: RequestBundleDir = RequestBundleDir(request_id)

    log.info("Configuring Nexus for RubyGems for the request %d", request_id)
    set_request_state(request_id, "in_progress", "Configuring Nexus for RubyGems")
    rubygems_repo_name = get_rubygems_hosted_repo_name(request_id)
    prepare_nexus_for_rubygems_request(rubygems_repo_name)

    log.info("Fetching dependencies for request %d", request_id)
    package_configs = package_configs or [{}]
    packages_data = []
    for pkg_cfg in package_configs:
        pkg_path = os.path.normpath(pkg_cfg.get("path", "."))
        package_source_dir = bundle_dir.app_subpath(pkg_path).source_dir
        set_request_state(
            request_id,
            "in_progress",
            f"Fetching dependencies at the {pkg_path!r} directory",
        )
        request = get_request(request_id)
        pkg_and_deps_info = resolve_rubygems(
            package_source_dir,
            request,
        )

        packages_data.append(pkg_and_deps_info)

    log.info("Finalizing the Nexus configuration for RubyGems for the request %d", request_id)
    set_request_state(request_id, "in_progress", "Finalizing the Nexus configuration for RubyGems")
    username = get_rubygems_nexus_username(request_id)
    password = finalize_nexus_for_rubygems_request(rubygems_repo_name, username)
    hosted_url = get_rubygems_hosted_url_with_credentials(username, password, request_id)

    rubygems_config_files = []
    ca_cert = nexus.get_ca_cert()
    if ca_cert:
        ca_cert_path = os.path.join("app", "rubygems-proxy-ca.pem")
        rubygems_config_files.append(make_base64_config_file(ca_cert, ca_cert_path))
    else:
        ca_cert_path = None

    for pkg_data, pkg_cfg in zip(packages_data, package_configs):
        pkg_path = os.path.normpath(pkg_cfg.get("path", "."))
        package_source_dir = bundle_dir.app_subpath(pkg_path).source_dir
        config_file = _get_config_file_for_given_package(
            pkg_data["dependencies"], bundle_dir, package_source_dir, hosted_url, ca_cert_path
        )
        rubygems_config_files.append(config_file)

    packages_json_data = PackagesData()
    for pkg_data, pkg_cfg in zip(packages_data, package_configs):
        pkg_subpath = os.path.normpath(pkg_cfg.get("path", "."))
        pkg_info = pkg_data["package"]
        pkg_deps = cleanup_metadata(pkg_data["dependencies"])
        packages_json_data.add_package(pkg_info, pkg_subpath, pkg_deps)
    packages_json_data.write_to_file(bundle_dir.rubygems_packages_data)

    if rubygems_config_files:
        update_request_with_config_files(request_id, rubygems_config_files)


def _get_config_file_for_given_package(
    dependencies, bundle_dir, package_source_dir, rubygems_hosted_url, ca_cert_path
):
    """
    Get Bundler config file.

    Returns a Bundler config file with a mirror set for RubyGems dependencies pointing to
    `rubygems_hosted_repo` URL. All GIT dependencies are configured to be replaced by local git
     repos.

    :param dependencies: an array of dependencies (dictionaries) with keys
        "name": package name,
        "path": an absolute path to a locally downloaded git repo,
        "kind": dependency kind
    :param bundle_dir: an absolute path to the root of the Cachito bundle
    :param package_source_dir: a path to the root directory of given package
    :param rubygems_hosted_url: URL pointing to a request specific RubyGems hosted repo with
     hardcoded user credentials
    :param ca_cert_path: Path relative to bundle_dir to the Nexus CA certificate Bundler should use
    :return: dict with "content", "path" and "type" keys
    """
    base_config = dedent(
        f"""
        # Sets mirror for all RubyGems sources
        BUNDLE_MIRROR__ALL: "{rubygems_hosted_url}"
        # Turn off the probing
        BUNDLE_MIRROR__ALL__FALLBACK_TIMEOUT: "false"
        # Install only ruby platform gems (=> gems with native extensions are compiled from source).
        # All gems should be platform independent already, so why not keep it here.
        BUNDLE_FORCE_RUBY_PLATFORM: "true"
        BUNDLE_DEPLOYMENT: "true"
        # Defaults to true when deployment is set to true
        BUNDLE_FROZEN: "true"
        # For local Git replacements, branches don't have to be specified (commit hash is enough)
        BUNDLE_DISABLE_LOCAL_BRANCH_CHECK: "true"
    """
    )

    config = [base_config]

    if ca_cert_path:
        rel_ca_cert_path = relpath(Path(bundle_dir / ca_cert_path), package_source_dir)
        config.append(f"BUNDLE_SSL_CA_CERT: {rel_ca_cert_path}")

    for dependency in dependencies:
        if dependency["kind"] == "GIT":
            # These substitutions are required by Bundler
            name = dependency["name"].upper().replace("-", "___").replace(".", "__")
            relative_path = relpath(dependency["path"], package_source_dir)
            dep_replacement = f'BUNDLE_LOCAL__{name}: "{relative_path + "/app"}"'
            config.append(dep_replacement)

    final_config = "\n".join(config)

    config_file_path = package_source_dir / Path(".bundle/config")
    if config_file_path.exists():
        raise CachitoError(
            f"Cachito wants to create a config file at location {config_file_path}, "
            f"but it already exists."
        )
    final_path = config_file_path.relative_to(Path(bundle_dir))
    return make_base64_config_file(final_config, final_path)


def cleanup_metadata(dependencies: list[dict]):
    """
    For each dependency, keep only metadata specified in docs/metadata.md for Request JSON.

    :param list dependencies: which should be processed
    :return: list of dependencies where each dependency is represented by a dictionary
        with the following keys: name, version, type
    :rtype list[dict]:
    """
    return [
        {"name": dep["name"], "version": dep["version"], "type": dep["type"]}
        for dep in dependencies
    ]
