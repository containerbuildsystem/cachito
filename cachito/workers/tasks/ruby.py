# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import random
import secrets
from typing import List

from cachito.errors import CachitoError
from cachito.workers import nexus
from cachito.workers.config import get_worker_config, validate_rubygems_config
from cachito.workers.errors import NexusScriptError
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general import (
    update_request_with_config_files,
    update_request_with_deps,
    update_request_with_package,
)
from cachito.workers.pkg_managers.ruby import (
    generate_bundle_config_content,
    get_bundler_proxy_repo_name,
    get_bundler_proxy_repo_url,
    get_bundler_proxy_repo_username,
    resolve_bundler,
)
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.general import set_request_state
from cachito.workers.tasks.utils import make_base64_config_file, AssertPackageFiles

__all__ = ["cleanup_rubygems_request", "fetch_rubygems_source"]

log = logging.getLogger(__name__)


@app.task
def cleanup_rubygems_request(request_id):
    """Clean up the Nexus rubygems content for the Cachito request."""
    payload = {
        "rubygems_repository_name": get_bundler_proxy_repo_name(request_id),
        "username": get_bundler_proxy_repo_username(request_id),
    }
    nexus.execute_script("rubygems_cleanup", payload)


def finalize_nexus_for_rubygems_request(repo_name, username):
    """
    Finalize the Nexus configuration so that the request's rubygems repository is ready for consumption.

    :param str repo_name: the name of the repository for the request for this package manager
    :param str username: the username of the user to be created for the request for this package
        manager
    :return: the password of the Nexus user that has access to the request's rubygems repository
    :rtype: str
    :raise CachitoError: if the script execution fails
    """
    # Generate a 24-32 character (each byte is two hex characters) password
    password = secrets.token_hex(random.randint(12, 16))
    payload = {"password": password, "rubygems_repository_name": repo_name, "username": username}
    script_name = "rubygems_after_content_staged"
    try:
        nexus.execute_script(script_name, payload)
    except NexusScriptError:
        log.exception("Failed to execute the script %s", script_name)
        raise CachitoError(
            "Failed to configure Nexus to allow the request's rubygems repository to be ready for "
            "consumption"
        )
    return password


def prepare_nexus_for_rubygems_request(repo_name):
    """
    Prepare Nexus so that Cachito can stage Ruby Gems content.

    :param str repo_name: the name of the repository for the request for this package manager
    :raise CachitoError: if the script execution fails
    """
    config = get_worker_config()
    # Note that the http_username and http_password represent the unprivileged user that
    # the new Nexus rubygems proxy repository will use to connect to the "cachito-rubygems" Nexus group
    # repository
    payload = {
        "rubygems_repository_name": repo_name,
        "http_password": config.cachito_nexus_proxy_password,
        "http_username": config.cachito_nexus_proxy_username,
        "rubygems_proxy_url": config.cachito_nexus_rubygems_proxy_repo_url,
    }
    script_name = "rubygems_before_content_staged"
    try:
        nexus.execute_script(script_name, payload)
    except NexusScriptError:
        log.exception(f"Failed to execute the script {script_name}")
        raise CachitoError("Failed to prepare Nexus for Cachito to stage JavaScript content")


def _verify_bundler_files(bundle_dir: RequestBundleDir, subpaths: List[str]):
    """
    Verify that the expected bundler files are present for the Ruby Bundler package manager to proceed.

    For each subpath:
    - Gemfile must be present
    - Gemfile.lock must be present
    - vendor/ must not be present

    :param RequestBundleDir bundle_dir: the ``RequestBundleDir`` object for the request
    :param list[str] subpaths: a list of subpaths in the source repository of yarn packages
    :raises CachitoError: if the repository is missing the required files or contains invalid
        files/directories
    """
    for subpath in subpaths:
        assert_files = AssertPackageFiles("bundler", bundle_dir.source_root_dir, package_path=subpath)
        assert_files.present("Gemfile")
        assert_files.present("Gemfile.lock")
        # TODO: what we really want to check is that the vendor directory is empty. It's ok if it exists.
        # assert_files.dir_absent("vendor")


def generate_bundler_config_files(
    proxy_repo_url: str, username: str, password: str, subpaths: List[str],
) -> List[dict]:
    """
    Generate one .bundle/config file for each subpath in request.

    If Nexus has a CA cert, it will also be added as a configuration file.

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

        bundle_config = generate_bundle_config_content(
            proxy_repo_url, username, password, custom_ca_path=custom_ca_path
        )
        bundle_config_path = os.path.normpath(os.path.join("app", subpath, ".bundle/config"))
        config_files.append(make_base64_config_file(bundle_config, bundle_config_path))

    return config_files


@app.task
def fetch_rubygems_source(request_id: int, package_configs: List[dict] = None):
    """
    Resolve and fetch ruby gems dependencies for a given request.

    This function uses the Python ``os.path`` library to manipulate paths, so the path to the
    configuration files may differ in format based on the system the Cachito worker is deployed on
    (i.e. Linux vs Windows).

    :param int request_id: the Cachito request ID this is for
    :param list package_configs: the list of optional package configurations submitted by the user
    :raise CachitoError: if the task fails
    """
    if package_configs is None:
        package_configs = []

    validate_rubygems_config()

    bundle_dir = RequestBundleDir(request_id)
    subpaths = [os.path.normpath(c["path"]) for c in package_configs if c.get("path")]

    if not subpaths:
        # Default to the root of the application source
        subpaths = [os.curdir]

    _verify_bundler_files(bundle_dir, subpaths)

    log.info("Configuring Nexus for bundler for the request %d", request_id)
    set_request_state(request_id, "in_progress", "Configuring Nexus for bundler")
    repo_name = get_bundler_proxy_repo_name(request_id)
    prepare_nexus_for_rubygems_request(repo_name)

    bundler_config_files = []
    downloaded_deps = set()
    for i, subpath in enumerate(subpaths):
        log.info("Fetching the ruby gem dependencies for request %d in subpath %s", request_id, subpath)
        request = set_request_state(
            request_id,
            "in_progress",
            f'Fetching the ruby gem dependencies at the "{subpath}" directory',
        )
        package_source_path = str(bundle_dir.app_subpath(subpath).source_dir)
        try:
            package_and_deps_info = resolve_bundler(
                package_source_path, request, skip_deps=downloaded_deps
            )
        except CachitoError:
            log.exception("Failed to fetch bundler dependencies for request %d", request_id)
            raise

        # downloaded_deps = downloaded_deps | package_and_deps_info["downloaded_deps"]

        log.info(
            "Generating the bundler configuration files for request %d in subpath %s",
            request_id,
            subpath,
        )

        if i == 0:
            default_env = get_worker_config().cachito_default_environment_variables
            env_vars = {**default_env.get("bundler", {})}
        else:
            env_vars = None

        package = package_and_deps_info["package"]
        update_request_with_package(request_id, package, env_vars, package_subpath=subpath)
        update_request_with_deps(request_id, package, package_and_deps_info["deps"])

    log.info("Finalizing the Nexus configuration for bundler for the request %d", request_id)
    set_request_state(request_id, "in_progress", "Finalizing the Nexus configuration for bundler")
    username = get_bundler_proxy_repo_username(request_id)
    password = finalize_nexus_for_rubygems_request(username, repo_name)

    log.info("Generating the .bundle/config file(s)")
    proxy_repo_url = get_bundler_proxy_repo_url(request_id)
    bundler_config_files.extend(
        generate_bundler_config_files(proxy_repo_url, username, password, subpaths)
    )

    update_request_with_config_files(request_id, bundler_config_files)
