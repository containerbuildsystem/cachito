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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from cachito.errors import CachitoError
from cachito.workers import nexus, run_cmd
from cachito.workers.config import get_worker_config
from cachito.workers.errors import NexusScriptError
from cachito.workers.pkg_managers.general import ChecksumInfo, verify_checksum

__all__ = [
    "download_dependencies",
    "finalize_nexus_for_js_request",
    "find_package_json",
    "generate_and_write_npmrc_file",
    "generate_npmrc_content",
    "get_js_hosted_repo_name",
    "get_npm_component_info_from_nexus",
    "prepare_nexus_for_js_request",
    "upload_non_registry_dependency",
    "process_non_registry_dependency",
    "JSDependency",
]

log = logging.getLogger(__name__)


def download_dependencies(
    download_dir: Path,
    deps: List[Dict[str, Any]],
    proxy_repo_url: str,
    skip_deps: Optional[Set[str]] = None,
    pkg_manager: str = "npm",
) -> Set[str]:
    """
    Download the list of npm dependencies using npm pack to the deps bundle directory.

    By downloading the dependencies, this stages the content in the request specific npm proxy.

    Any dependency that has the key "bundled" set to ``True`` will not be downloaded. This is
    because the dependency is bundled as part of another dependency, and thus already present in
    the tarball of the dependency that bundles it.

    :param download_dir: the downloaded tarball of each dependency will be stored under this
        directory with necessary parent directory components created. For example, the tarball
        of a dependency foo is stored under <download_dir>/github/repo_namespace/foo.tar.gz
    :type download_dir: pathlib.Path
    :param deps: a list of dependencies where each dependency has the keys: bundled, name,
        version, and version_in_nexus
    :type deps: list[dict[str, any]]
    :param str proxy_repo_url: the Nexus proxy repository URL to use as the registry
    :param set[str] skip_deps: a set of dependency identifiers to not download because they've
        already been downloaded for this request.
    :param str pkg_manager: the name of the package manager to download dependencies for, affects
        destination directory and logging output (npm is used to do the actual download regardless)
    :return: a set of dependency identifiers that were downloaded
    :rtype: set[str]
    :raises CachitoError: if any of the downloads fail
    """
    assert pkg_manager == "npm" or pkg_manager == "yarn"  # nosec

    if skip_deps is None:
        skip_deps = set()

    conf = get_worker_config()
    with tempfile.TemporaryDirectory(prefix="cachito-") as temp_dir:
        npm_rc_file = os.path.join(temp_dir, ".npmrc")
        if conf.cachito_nexus_ca_cert and os.path.exists(conf.cachito_nexus_ca_cert):
            nexus_ca = conf.cachito_nexus_ca_cert
        else:
            nexus_ca = None
        # The token must be privileged so that it has access to the cachito-js repository
        generate_and_write_npmrc_file(
            npm_rc_file,
            proxy_repo_url,
            conf.cachito_nexus_username,
            conf.cachito_nexus_password,
            custom_ca_path=nexus_ca,
        )
        env = {
            # This is set since the home directory must be determined by the HOME environment
            # variable or by looking at the /etc/passwd file. The latter does not always work
            # since some deployments (e.g. OpenShift) don't have an entry for the running user
            # in /etc/passwd.
            "HOME": os.environ.get("HOME", ""),
            "NPM_CONFIG_CACHE": os.path.join(temp_dir, "cache"),
            # This should not be necessary since all the dependencies come from Nexus, but it's an
            # extra precaution
            "NPM_CONFIG_IGNORE_SCRIPTS": "true",
            "NPM_CONFIG_USERCONFIG": npm_rc_file,
            "PATH": os.environ.get("PATH", ""),
        }
        # Download the dependencies directly in the bundle directory
        run_params = {"env": env, "cwd": str(download_dir)}
        log.info("Processing %d %s dependencies to stage in Nexus", len(deps), pkg_manager)
        downloaded_deps = set()
        # This must be done in batches to prevent Nexus from erroring with "Header is too large"
        deps_batches: List[List] = []
        counter = 0
        batch_size = get_worker_config().cachito_js_download_batch_size
        for dep in deps:
            external_dep_version = None
            if dep.get("version_in_nexus"):
                version = dep["version_in_nexus"]
                external_dep_version = dep["version"]
            else:
                version = dep["version"]

            dep_identifier = f"{dep['name']}@{version}"

            if dep["bundled"]:
                log.debug("Not downloading %s since it is a bundled dependency", dep_identifier)
                continue
            elif dep["version"].startswith("file:"):
                log.debug("Not downloading %s since it is a file dependency", dep_identifier)
                continue
            elif dep_identifier in skip_deps:
                log.debug(
                    "Not downloading %s since it was already downloaded previously", dep_identifier
                )
                continue

            if counter % batch_size == 0:
                deps_batches.append([])
            deps_batches[-1].append((dep_identifier, external_dep_version))
            downloaded_deps.add(dep_identifier)
            counter += 1

        for dep_batch in deps_batches:
            # Create a list of dependencies to be downloaded. Excluding 'external_dep_version'
            # from the list of tuples
            dep_identifiers = [dep_identifier for dep_identifier, _ in dep_batch]
            log.debug(
                "Downloading the following %s dependencies: %s",
                pkg_manager,
                ", ".join(dep_identifiers),
            )
            npm_pack_args = ["npm", "pack"] + dep_identifiers
            output = run_cmd(
                npm_pack_args, run_params, f"Failed to download the {pkg_manager} dependencies"
            )

            # Move dependencies to their respective folders
            # Iterate through the tuples made of dependency tarball and dep_identifier
            # e.g. ('ab-2.10.2-external-sha512-ab.tar.gz', ('ab@2.10.2-external-sha512-ab',
            # 'https://github.com/ab/2.10.2.tar.gz'))
            for tarball, (dep_identifier, external_dep_version) in zip(
                output.split("\n"), dep_batch
            ):
                # tarball: e.g. ab-2.10.2-external-sha512-ab.tar.gz
                # dep_identifier: ab@2.10.2-external-sha512-ab
                # external_dep_version:  https://github.com/ab/2.10.2.tar.gz
                dir_path = dep_identifier.rsplit("@", 1)[0]  # ab

                # In case of external dependencies, create additional intermediate
                # parent e.g. github/<org>/<repo> or external-<repo>
                if external_dep_version:
                    known_git_host_match = re.match(
                        r"^(?P<host>.+)(?::)(?!//)(?P<repo_path>.+)(?:#.+)$", external_dep_version
                    )
                    if known_git_host_match:
                        # This means external_dep_version is in the format of
                        # <git-host>:<namespace>/<repo>#<commit>
                        groups = known_git_host_match.groupdict()
                        dir_path = os.path.join(groups["host"], *groups["repo_path"].split("/"))
                    else:
                        dir_path = f"external-{dir_path}"

                # Create the target directory for the dependency
                dep_dir = download_dir.joinpath(*dir_path.split("/", 1))
                dep_dir.mkdir(exist_ok=True, parents=True)
                # Move the dependency into the target directory
                shutil.move(str(download_dir.joinpath(tarball)), str(dep_dir.joinpath(tarball)))

        return downloaded_deps


def finalize_nexus_for_js_request(repo_name, username):
    """
    Finalize the Nexus configuration so that the request's npm repository is ready for consumption.

    :param str repo_name: the name of the repository for the request for this package manager
    :param str username: the username of the user to be created for the request for this package
        manager
    :return: the password of the Nexus user that has access to the request's npm repository
    :rtype: str
    :raise CachitoError: if the script execution fails
    """
    # Generate a 24-32 character (each byte is two hex characters) password
    password = secrets.token_hex(random.randint(12, 16))  # nosec
    payload = {"password": password, "repository_name": repo_name, "username": username}
    script_name = "js_after_content_staged"
    try:
        nexus.execute_script(script_name, payload)
    except NexusScriptError:
        log.exception("Failed to execute the script %s", script_name)
        raise CachitoError(
            "Failed to configure Nexus to allow the request's npm repository to be ready for "
            "consumption"
        )
    return password


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


def generate_and_write_npmrc_file(
    npm_rc_path, proxy_repo_url, username, password, custom_ca_path=None
):
    """
    Generate a .npmrc file at the input location with the registry and authentication configured.

    :param str npm_rc_path: the path to create the .npmrc file
    :param str proxy_repo_url: the npm registry URL
    :param str username: the username of the user to use for authenticating to the registry
    :param str password: the password of the user to use for authenticating to the registry
    :param str custom_ca_path: the path to set ``cafile`` to in the .npm rc file; if not provided,
        this option will be omitted
    """
    log.debug("Generating a .npmrc file at %s", npm_rc_path)
    with open(npm_rc_path, "w") as f:
        f.write(
            generate_npmrc_content(
                proxy_repo_url, username, password, custom_ca_path=custom_ca_path
            )
        )


def generate_npmrc_content(proxy_repo_url, username, password, custom_ca_path=None):
    """
    Generate a .npmrc file with the registry and authentication configured.

    :param str proxy_repo_url: the npm registry URL
    :param str username: the username of the user to use for authenticating to the registry
    :param str password: the password of the user to use for authenticating to the registry
    :param str custom_ca_path: the path to set ``cafile`` to in the .npm rc file; if not provided,
        this option will be omitted
    :return: the contents of the .npmrc file
    :rtype: str
    """
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
    config = get_worker_config()
    return config.cachito_nexus_js_hosted_repo_name


def _get_js_component_info_from_nexus(
    name: str, version: str, repository: str, is_hosted: bool, max_attempts: int = 1
) -> Optional[dict]:
    """
    Get the JS component information a Nexus repository using Nexus' REST API.

    :param str name: the name of the dependency including the scope if present
    :param str version: the version of the dependency; a wildcard can be specified but it should
        not match more than a single version
    :param str repository: the name of the Nexus repository to get information from
    :param bool is_hosted: is the repository in the hosted Nexus instance?
    :param int max_attempts: the number of attempts to try to get a result; this defaults to ``1``
    :return: the JSON about the NPM component or None
    :rtype: dict or None
    :raise CachitoError: if the search fails or more than one component is returned
    """
    if name.startswith("@"):
        component_group_with_prefix, component_name = name.split("/", 1)
        # Remove the "@" prefix
        component_group: Union[str, object] = component_group_with_prefix[1:]
    else:
        component_name = name
        component_group = nexus.NULL_GROUP

    return nexus.get_component_info_from_nexus(
        repository,
        "npm",
        component_name,
        version,
        component_group,
        max_attempts,
        from_nexus_hoster=is_hosted,
    )


def get_npm_component_info_from_nexus(
    name: str, version: str, max_attempts: int = 1
) -> Optional[dict]:
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
    return _get_js_component_info_from_nexus(
        name, version, get_js_hosted_repo_name(), is_hosted=True, max_attempts=max_attempts
    )


def get_yarn_component_info_from_non_hosted_nexus(
    name: str, version: str, repository: str, max_attempts: int = 1
) -> Optional[dict]:
    """
    Get the Yarn component information from a non-hosted Nexus repository.

    :param str name: the name of the dependency including the scope if present
    :param str version: the version of the dependency; a wildcard can be specified but it should
        not match more than a single version
    :param str repository: the name of the non-hosted Nexus repository to get information from
    :param int max_attempts: the number of attempts to try to get a result; this defaults to ``1``
    :return: the JSON about the Yarn component or None
    :rtype: dict or None
    :raise CachitoError: if the search fails or more than one component is returned
    """
    return _get_js_component_info_from_nexus(
        name, version, repository, is_hosted=False, max_attempts=max_attempts
    )


def prepare_nexus_for_js_request(repo_name):
    """
    Prepare Nexus so that Cachito can stage JavaScript content.

    :param str repo_name: the name of the repository for the request for this package manager
    :raise CachitoError: if the script execution fails
    """
    config = get_worker_config()
    # Note that the http_username and http_password represent the unprivileged user that
    # the new Nexus npm proxy repository will use to connect to the "cachito-js" Nexus group
    # repository
    payload = {
        "repository_name": repo_name,
        "http_password": config.cachito_nexus_proxy_password,
        "http_username": config.cachito_nexus_proxy_username,
        "npm_proxy_url": config.cachito_nexus_npm_proxy_url,
    }
    script_name = "js_before_content_staged"
    try:
        nexus.execute_script(script_name, payload)
    except NexusScriptError:
        log.exception(f"Failed to execute the script {script_name}")
        raise CachitoError("Failed to prepare Nexus for Cachito to stage JavaScript content")


def upload_non_registry_dependency(
    dep_identifier, version_suffix, verify_scripts=False, checksum_info=None
):
    """
    Upload the non-registry npm dependency to the Nexus hosted repository with a custom version.

    :param str dep_identifier: the identifier of the dependency to download
    :param str version_suffix: the suffix to append to the dependency's version in its package.json
        file
    :param bool verify_scripts: if ``True``, raise an exception if dangerous scripts are present in
        the ``package.json`` file and would have been executed by ``npm pack`` if ``ignore-scripts``
        was set to ``false``
    :param ChecksumInfo checksum_info: if not ``None``, the checksum of the downloaded artifact
        will be verified.
    :raise CachitoError: if the dependency cannot be download, uploaded, or is invalid
    """
    # These are the scripts that should not be present if verify_scripts is True
    dangerous_scripts = {"prepare", "prepack"}
    with tempfile.TemporaryDirectory(prefix="cachito-") as temp_dir:
        env = {
            # This is set since the home directory must be determined by the HOME environment
            # variable or by looking at the /etc/passwd file. The latter does not always work
            # since some deployments (e.g. OpenShift) don't have an entry for the running user
            # in /etc/passwd.
            "HOME": os.environ.get("HOME", ""),
            "NPM_CONFIG_CACHE": os.path.join(temp_dir, "cache"),
            # This is important to avoid executing any dangerous scripts if it's a Git dependency
            "NPM_CONFIG_IGNORE_SCRIPTS": "true",
            "PATH": os.environ.get("PATH", ""),
            # Have `npm pack` fail without a prompt if the SSH key from a protected source such
            # as a private GitHub repo is not trusted
            "GIT_SSH_COMMAND": "ssh -o StrictHostKeyChecking=yes",
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
        if checksum_info:
            verify_checksum(dep_archive, checksum_info)

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

                    if verify_scripts:
                        log.info(
                            "Checking for dangerous scripts in the package.json of %s",
                            dep_identifier,
                        )
                        scripts = package_json.get("scripts", {})
                        if dangerous_scripts & scripts.keys():
                            msg = (
                                f"The dependency {dep_identifier} is not supported because Cachito "
                                "cannot execute the following required scripts of Git "
                                f"dependencies: {', '.join(sorted(dangerous_scripts))}"
                            )
                            log.error(msg)
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
        nexus.upload_asset_only_component(repo_name, "npm", modified_dep_archive)


@dataclass(frozen=True)
class JSDependency:
    """Holds package-manager-independent data about a JavaScript dependency."""

    # The name of the dependency
    # In package-lock.json these are the keys in the "dependencies" object
    # In yarn.lock, these are the top-level keys in the file (the keys are <name>@<version>)
    name: str

    # The source of the dependency, i.e. resolved url or relative path.
    # In package-lock.json, this is either the "resolved" key or the "version" key
    # In yarn.lock, this is either the "resolved" key or the filepath in the top-level key
    source: str

    # The actual semver version of the dependency
    # In package-lock.json, this either the "version" key or not present
    # In yarn.lock, this is always the "version" key
    version: Optional[str] = None

    # The hash algorithm and base64-encoded checksum of the dependency
    # In package-lock.json, this is the "integrity" key and will always be present for registry
    # deps and tarball deps (it is not relevant for the other types).
    # In yarn.lock, the "integrity" key seems to be present only for registry deps by default, but
    # all resolved urls also have a SHA1 checksum in the fragment part. It is unclear how yarn
    # decides whether to check the integrity value or the checksum in the url when both are present.
    integrity: Optional[str] = None

    @property
    def qualified_name(self):
        """
        Get the <name>@<source> of this dependency.

        Used primarily as user-facing representation of non-registry dependencies.
        """
        return f"{self.name}@{self.source}"


def process_non_registry_dependency(js_dep):
    """
    Convert the input dependency not from the NPM registry to a Nexus hosted dependency.

    :param JSDependency js_dep: the dependency to be converted
    :return: information about the replacement dependency in Nexus
    :rtype: JSDependency
    :raise CachitoError: if the dependency is from an unsupported location
        or has an unexpected format
    """
    git_prefixes = {
        "git://",
        "git+http://",
        "git+https://",
        "git+ssh://",
        "github:",
        "bitbucket:",
        "gitlab:",
    }
    http_prefixes = {"http://", "https://"}
    verify_scripts = False
    checksum_info = None
    if any(js_dep.source.startswith(prefix) for prefix in git_prefixes):
        try:
            _, commit_hash = js_dep.source.rsplit("#", 1)
        except ValueError:
            msg = (
                f"The url for the dependency {js_dep.qualified_name} was in an unexpected format "
                "(expected <git_url>#<commit_hash>)"
            )
            log.error(msg)
            raise CachitoError(msg)
        # When the dependency is uploaded to the Nexus hosted repository, it will be in the format
        # of `<version>-gitcommit-<commit hash>`
        version_suffix = f"-external-gitcommit-{commit_hash}"
        # Dangerous scripts might be required to be executed by `npm pack` since this is a Git
        # dependency. If those scripts are present, Cachito will fail the request since it will not
        # execute those scripts when packing the dependency.
        verify_scripts = True
    elif any(js_dep.source.startswith(prefix) for prefix in http_prefixes):
        if not js_dep.integrity:
            msg = (
                f"The dependency {js_dep.qualified_name} is missing the integrity value. "
                'Is the "integrity" key missing in your lockfile?'
            )
            log.error(msg)
            raise CachitoError(msg)

        checksum_info = convert_integrity_to_hex_checksum(js_dep.integrity)
        # When the dependency is uploaded to the Nexus hosted repository, it will be in the format
        # of `<version>-external-<checksum algorithm>-<hex checksum>`
        version_suffix = f"-external-{checksum_info.algorithm}-{checksum_info.hexdigest}"
    else:
        raise CachitoError(
            f"The dependency {js_dep.qualified_name} is hosted in an unsupported location"
        )

    component_info = get_npm_component_info_from_nexus(js_dep.name, f"*{version_suffix}")
    if not component_info:
        upload_non_registry_dependency(js_dep.source, version_suffix, verify_scripts, checksum_info)
        component_info = get_npm_component_info_from_nexus(
            js_dep.name, f"*{version_suffix}", max_attempts=5
        )
        if not component_info:
            raise CachitoError(
                f"The dependency {js_dep.qualified_name} was uploaded to Nexus but is not "
                "accessible"
            )

    return JSDependency(
        name=js_dep.name,
        source=component_info["assets"][0]["downloadUrl"],
        version=component_info["version"],
        integrity=convert_hex_sha_to_npm(
            component_info["assets"][0]["checksum"]["sha512"], "sha512"
        ),
    )


def convert_hex_sha_to_npm(hex_sha, algorithm):
    """
    Convert the input sha checksum in hex to the format an npm/yarn lock file uses.

    Note: does not verify that the input checksum is valid for the specified algorithm.

    :param str hex_sha: the sha checksum in hex
    :param str algorithm: the sha algorithm to use
    :return: the sha checksum in npm/yarn lock file format
    :rtype: str
    """
    bytes_sha = bytes.fromhex(hex_sha)
    base64_sha = base64.b64encode(bytes_sha).decode("utf-8")
    return f"{algorithm}-{base64_sha}"


def convert_integrity_to_hex_checksum(integrity):
    """
    Convert the input integrity value of a dependency to a hex checksum.

    The integrity is a key in an npm/yarn lock file that contains the checksum of the dependency
    in the format of ``<algorithm>-<base64 of the binary hash>``.

    :param str integrity: the integrity from the npm/yarn lock file
    :return: a tuple where the first item is the algorithm used and second is the hex value of
        the checksum
    :rtype: (str, str)
    """
    algorithm, checksum = integrity.split("-", 1)
    return ChecksumInfo(algorithm, base64.b64decode(checksum).hex())
