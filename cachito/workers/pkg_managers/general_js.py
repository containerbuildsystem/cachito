# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import io
import json
import logging
import os
import random
import re
import secrets
import shutil
import tarfile
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
    "find_package_json",
    "generate_and_write_npmrc_file",
    "generate_npmrc_content",
    "get_js_hosted_repo_name",
    "get_js_proxy_repo_name",
    "get_js_proxy_repo_url",
    "get_js_proxy_username",
    "get_npm_component_info_from_nexus",
    "prepare_nexus_for_js_request",
    "upload_non_registry_dependency",
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
        version, and version_in_nexus
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
            # Have `npm pack` fail without a prompt if the SSH key from a protected source such
            # as a private GitHub repo is not trusted
            "GIT_SSH_COMMAND": "ssh -o StrictHostKeyChecking=yes",
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
            if dep.get("version_in_nexus"):
                version = dep["version_in_nexus"]
            else:
                version = dep["version"]

            dep_identifier = f"{dep['name']}@{version}"

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
            output = run_cmd(npm_pack_args, run_params, "Failed to download the npm dependencies")

            # Move dependencies to their respective folders
            # Iterate through the tuples made of dependency tarball and dep_identifier
            # e.g. ('angular-animations-8.2.0.tgz', '@angular/animations@8.2.0')
            for dep_pair in list(zip(output.split("\n"), dep_batch)):
                tarball = dep_pair[0]  # e.g. angular-animations-8.2.0.tgz
                dir_path = dep_pair[1].rsplit("@", 1)[0]  # e.g. @angular/animations
                # Create the target directory for the dependency
                dep_dir = bundle_dir.npm_deps_dir.joinpath(*dir_path.split("/", 1))
                dep_dir.mkdir(exist_ok=True, parents=True)
                # Move the dependency into the target directory
                shutil.move(bundle_dir.npm_deps_dir.joinpath(tarball), dep_dir.joinpath(tarball))


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


def find_package_json(package_archive):
    """
    Find the package.json in a tar achive of an npm package.

    This logic is based on that of the npm CLI. If yarn support is added to Cachito, this may need
    to be adjusted if the algorithm is different.

    The npm CLI will parse a tar archive stream of the npm package.
      https://github.com/npm/cli/blob/cf7da1e1a0dc9becbe382ac5abd8830551009a53/node_modules/pacote/lib/finalize-manifest.js#L151
      https://github.com/npm/cli/blob/cf7da1e1a0dc9becbe382ac5abd8830551009a53/node_modules/pacote/lib/finalize-manifest.js#L154
    It then gets the package.json contents by callling ``jsonFromStream``.
      https://github.com/npm/cli/blob/cf7da1e1a0dc9becbe382ac5abd8830551009a53/node_modules/pacote/lib/finalize-manifest.js#L151
    In ``jsonFromStream``, for each entry in the tar archive, where I assume the ordering in the
    archive is preserved, search for a package.json file that is one or less levels deep in the
    directory tree. Once found, it will read the package.json file.
      https://github.com/npm/cli/blob/cf7da1e1a0dc9becbe382ac5abd8830551009a53/node_modules/pacote/lib/finalize-manifest.js#L200-L218
    If no package.json file is found, an error is thrown.
      https://github.com/npm/cli/blob/cf7da1e1a0dc9becbe382ac5abd8830551009a53/node_modules/pacote/lib/finalize-manifest.js#L156-L160

    :param str package_archive: the path to the tar archive of the npm package
    :return: the path to the package.json file in the tarball or None
    :rtype: str or None
    """
    log.debug("Finding the package.json file in the archive %s", package_archive)
    with tarfile.open(package_archive, "r:*") as f:
        # Iterate through all the members of the tar archive in order
        for member in f.getmembers():
            # If one or more directories are present in the tar archive, remove the first directory
            # and then check if the value is equal to package.json
            #   https://github.com/npm/cli/blob/cf7da1e1a0dc9becbe382ac5abd8830551009a53/node_modules/pacote/lib/finalize-manifest.js#L201-L204
            if re.sub(r"[^/]+/", "", member.name, count=1) == "package.json":
                log.debug(
                    "Found the package.json file at %s in the archive %s",
                    member.name,
                    package_archive,
                )
                return member.name


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


def get_js_hosted_repo_name():
    """
    Get the name of NPM hosted repository.

    :return: the name of NPM hosted repository
    :rtype: str
    """
    return "cachito-js-hosted"


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


def get_npm_component_info_from_nexus(name, version, max_attempts=1):
    """
    Get the NPM component information from the NPM hosted repository using Nexus' REST API.

    :param str name: the name of the dependency including the scope if present
    :param str version: the version of the dependency; a wildcard can be specified but it should
        not match more than a single version
    :param int max_attempts: the number of attempts to try to get a result; this defaults to ``1``
    :return: the JSON about the NPM component or None
    :rtype: dict or None
    :raise CachitoError: if the search fails or more than one component is returned
    """
    if name.startswith("@"):
        component_group, component_name = name.split("/", 1)
        # Remove the "@" prefix
        component_group = component_group[1:]
    else:
        component_name = name
        component_group = None

    repository = get_js_hosted_repo_name()
    return nexus.get_component_info_from_nexus(
        repository, "npm", component_name, version, component_group, max_attempts
    )


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


def upload_non_registry_dependency(dep_identifier, version_suffix):
    """
    Upload the non-registry npm dependency to the Nexus hosted repository with a custom version.

    :param str dep_identifier: the identifier of the dependency to download
    :param str version_suffix: the suffix to append to the dependency's version in its package.json
        file
    :raise CachitoError: if the dependency cannot be download, uploaded, or is invalid
    """
    with tempfile.TemporaryDirectory(prefix="cachito-") as temp_dir:
        env = {
            "NPM_CONFIG_CACHE": os.path.join(temp_dir, "cache"),
            "PATH": os.environ.get("PATH", ""),
        }
        run_params = {"env": env, "cwd": temp_dir}
        npm_pack_args = ["npm", "pack", dep_identifier]
        log.info("Downloading the npm dependency %s to be uploaded to Nexus", dep_identifier)
        # An example of the command's stdout:
        #   "reactivex-rxjs-6.5.5.tgz\n"
        stdout = run_cmd(
            npm_pack_args, run_params, f"Failed to download the npm dependency {dep_identifier}"
        )
        dep_archive = os.path.join(temp_dir, stdout.strip())

        package_json_rel_path = find_package_json(dep_archive)
        if not package_json_rel_path:
            msg = f"The dependency {dep_identifier} does not have a package.json file"
            log.error(msg)
            raise CachitoError(msg)

        modified_dep_archive = os.path.join(
            os.path.dirname(dep_archive), f"modified-{os.path.basename(dep_archive)}"
        )
        with tarfile.open(dep_archive, mode="r:*") as dep_archive_file:
            with tarfile.open(modified_dep_archive, mode="x:gz") as modified_dep_archive_file:
                for member in dep_archive_file.getmembers():
                    # Add all the files except for the package.json file without any modifications
                    if member.path != package_json_rel_path:
                        modified_dep_archive_file.addfile(
                            member, dep_archive_file.extractfile(member)
                        )
                        continue

                    # Modify the version in the package.json file
                    try:
                        package_json = json.load(dep_archive_file.extractfile(member))
                    except json.JSONDecodeError:
                        msg = (
                            f"The dependency {dep_identifier} does not have a valid "
                            "package.json file"
                        )
                        log.exception(msg)
                        raise CachitoError(msg)

                    new_version = f"{package_json['version']}{version_suffix}"
                    log.debug(
                        "Modifying the version of %s from %s to %s",
                        dep_identifier,
                        package_json["version"],
                        new_version,
                    )
                    package_json["version"] = new_version
                    package_json_bytes = json.dumps(package_json, indent=2).encode("utf-8")
                    package_json_file_obj = io.BytesIO(package_json_bytes)
                    member.size = len(package_json_bytes)
                    modified_dep_archive_file.addfile(member, package_json_file_obj)

        repo_name = get_js_hosted_repo_name()
        nexus.upload_artifact(repo_name, "npm", modified_dep_archive)
