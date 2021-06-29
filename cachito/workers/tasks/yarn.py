# SPDX-License-Identifier: GPL-3.0-or-later
import json
import logging
import os
from typing import List

import pyarn.lockfile

from cachito.common.packages_data import PackagesData
from cachito.errors import CachitoError
from cachito.workers import nexus
from cachito.workers.config import get_worker_config, validate_yarn_config
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general import (
    update_request_env_vars,
    update_request_with_config_files,
)
from cachito.workers.pkg_managers.general_js import (
    finalize_nexus_for_js_request,
    prepare_nexus_for_js_request,
)
from cachito.workers.pkg_managers.yarn import (
    get_yarn_proxy_repo_name,
    get_yarn_proxy_repo_url,
    get_yarn_proxy_repo_username,
    resolve_yarn,
)
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.npm import generate_npmrc_config_files
from cachito.workers.tasks.utils import (
    make_base64_config_file,
    AssertPackageFiles,
    runs_if_request_in_progress,
    get_request,
    set_request_state,
)

__all__ = ["cleanup_yarn_request", "fetch_yarn_source"]

log = logging.getLogger(__name__)


@app.task
def cleanup_yarn_request(request_id):
    """Clean up the Nexus yarn content for the Cachito request."""
    payload = {
        "repository_name": get_yarn_proxy_repo_name(request_id),
        "username": get_yarn_proxy_repo_username(request_id),
    }
    nexus.execute_script("js_cleanup", payload)


def _verify_yarn_files(bundle_dir: RequestBundleDir, subpaths: List[str]):
    """
    Verify that the expected yarn files are present for the yarn package manager to proceed.

    For each subpath:
    - package.json must be present
    - yarn.lock must be present
    - package-lock.json must not be present
    - npm-shrinkwrap.json must not be present
    - node_modules/ must not be present

    :param RequestBundleDir bundle_dir: the ``RequestBundleDir`` object for the request
    :param list[str] subpaths: a list of subpaths in the source repository of yarn packages
    :raises CachitoError: if the repository is missing the required files or contains invalid
        files/directories
    """
    for subpath in subpaths:
        assert_files = AssertPackageFiles("yarn", bundle_dir.source_root_dir, package_path=subpath)
        assert_files.present("package.json")
        assert_files.present("yarn.lock")
        assert_files.absent("package-lock.json")
        assert_files.absent("npm-shrinkwrap.json")
        assert_files.dir_absent("node_modules")


def _yarn_lock_to_str(yarn_lock_data: dict) -> str:
    """Convert yarn.lock data to string."""
    lockfile = pyarn.lockfile.Lockfile("1", yarn_lock_data)
    return lockfile.to_str()


@app.task
@runs_if_request_in_progress
def fetch_yarn_source(request_id: int, package_configs: List[dict] = None):
    """
    Resolve and fetch yarn dependencies for a given request.

    This function uses the Python ``os.path`` library to manipulate paths, so the path to the
    configuration files may differ in format based on the system the Cachito worker is deployed on
    (i.e. Linux vs Windows).

    :param int request_id: the Cachito request ID this is for
    :param list package_configs: the list of optional package configurations submitted by the user
    :raise CachitoError: if the task fails
    """
    if package_configs is None:
        package_configs = []

    validate_yarn_config()

    bundle_dir: RequestBundleDir = RequestBundleDir(request_id)
    subpaths = [os.path.normpath(c["path"]) for c in package_configs if c.get("path")]

    if not subpaths:
        # Default to the root of the application source
        subpaths = [os.curdir]

    _verify_yarn_files(bundle_dir, subpaths)

    log.info("Configuring Nexus for yarn for the request %d", request_id)
    set_request_state(request_id, "in_progress", "Configuring Nexus for yarn")
    repo_name = get_yarn_proxy_repo_name(request_id)
    prepare_nexus_for_js_request(repo_name)

    yarn_config_files = []
    downloaded_deps = set()
    packages_json_data = PackagesData()

    for i, subpath in enumerate(subpaths):
        log.info("Fetching the yarn dependencies for request %d in subpath %s", request_id, subpath)
        set_request_state(
            request_id,
            "in_progress",
            f'Fetching the yarn dependencies at the "{subpath}" directory',
        )
        request = get_request(request_id)
        package_source_path = str(bundle_dir.app_subpath(subpath).source_dir)
        try:
            package_and_deps_info = resolve_yarn(
                package_source_path, request, skip_deps=downloaded_deps
            )
        except CachitoError:
            log.exception("Failed to fetch yarn dependencies for request %d", request_id)
            raise

        downloaded_deps = downloaded_deps | package_and_deps_info["downloaded_deps"]

        log.info(
            "Generating the yarn configuration files for request %d in subpath %s",
            request_id,
            subpath,
        )
        remote_package_source_path = os.path.normpath(os.path.join("app", subpath))
        if package_and_deps_info["package.json"]:
            package_json_str = json.dumps(package_and_deps_info["package.json"], indent=2)
            package_json_path = os.path.join(remote_package_source_path, "package.json")
            yarn_config_files.append(make_base64_config_file(package_json_str, package_json_path))

        if package_and_deps_info["lock_file"]:
            yarn_lock_str = _yarn_lock_to_str(package_and_deps_info["lock_file"])
            yarn_lock_path = os.path.join(remote_package_source_path, "yarn.lock")
            yarn_config_files.append(make_base64_config_file(yarn_lock_str, yarn_lock_path))

        if i == 0:
            default_env = get_worker_config().cachito_default_environment_variables
            env_vars = {**default_env.get("npm", {}), **default_env.get("yarn", {})}
            update_request_env_vars(request_id, env_vars)

        pkg_info = package_and_deps_info["package"]
        pkg_deps = package_and_deps_info["deps"]
        packages_json_data.add_package(pkg_info, subpath, pkg_deps)

    packages_json_data.write_to_file(bundle_dir.yarn_packages_data)

    log.info("Finalizing the Nexus configuration for yarn for the request %d", request_id)
    set_request_state(request_id, "in_progress", "Finalizing the Nexus configuration for yarn")
    username = get_yarn_proxy_repo_username(request_id)
    password = finalize_nexus_for_js_request(username, repo_name)

    log.info("Generating the .npmrc file(s)")
    proxy_repo_url = get_yarn_proxy_repo_url(request_id)
    yarn_config_files.extend(
        generate_npmrc_config_files(proxy_repo_url, username, password, subpaths)
    )

    log.info("Adding empty .yarnrc file(s)")
    for subpath in subpaths:
        yarnrc_path = os.path.normpath(os.path.join("app", subpath, ".yarnrc"))
        yarn_config_files.append(make_base64_config_file("", yarnrc_path))

    update_request_with_config_files(request_id, yarn_config_files)
