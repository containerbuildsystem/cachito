import copy
import json
import logging
from os.path import normpath
from pathlib import Path
from urllib.parse import urlparse

import pyarn.lockfile

from cachito.errors import CachitoError
from cachito.workers.pkg_managers.general_js import (
    convert_hex_sha_to_npm,
    download_dependencies,
    process_non_registry_dependency,
    JSDependency,
)
from cachito.workers.config import get_worker_config

__all__ = [
    "get_yarn_proxy_repo_name",
    "get_yarn_proxy_repo_url",
    "get_yarn_proxy_repo_username",
    "resolve_yarn",
]

log = logging.getLogger(__name__)


NPM_REGISTRY_CNAMES = ("registry.npmjs.org", "registry.yarnpkg.com")


def get_yarn_proxy_repo_name(request_id):
    """
    Get the name of yarn proxy repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the cachito-yarn-<REQUEST_ID> string, representing the temporary repository name
    :rtype: str
    """
    config = get_worker_config()
    return f"{config.cachito_nexus_request_repo_prefix}yarn-{request_id}"


def get_yarn_proxy_repo_url(request_id):
    """
    Get the URL for the Nexus yarn proxy repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the URL for the Nexus cachito-yarn-<REQUEST_ID> repository
    :rtype: str
    """
    config = get_worker_config()
    repo_name = get_yarn_proxy_repo_name(request_id)
    return f"{config.cachito_nexus_url.rstrip('/')}/repository/{repo_name}/"


def get_yarn_proxy_repo_username(request_id):
    """
    Get the username that has read access on the yarn proxy repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the cachito-yarn-<REQUEST_ID> string, representing the user
        who will access the temporary Nexus repository
    :rtype: str
    """
    return f"cachito-yarn-{request_id}"


def _get_deps(yarn_lock, file_deps_allowlist):
    """
    Process the dependencies in a yarn.lock file and return relevant information.

    Will convert non-registry dependencies to Nexus hosted dependencies. If there are any
    non-registry dependencies, the yarn.lock file should later be modified to point to the
    replacement Nexus dependencies. This function returns all the information necessary to
    do that.

    The list of dependencies returned by this function is in a format that can be used directly
    as input to the general_js.download_dependencies function.

    :param dict yarn_lock: parsed yarn.lock data
    :param set file_deps_allowlist: an allow list of dependencies that are allowed to be "file"
        dependencies and should be ignored since they are implementation details
    :return: information about preprocessed dependencies and Nexus replacements
    :rtype: (list[dict], dict)
    :raise CachitoError: if the lock file contains a dependency from an unsupported location
    """
    deps = []
    nexus_replacements = {}

    for dep_identifier, dep_data in yarn_lock.items():
        package = pyarn.lockfile.Package.from_dict(dep_identifier, dep_data)

        if package.url:
            source = package.url
        elif package.relpath:
            source = f"file:{package.relpath}"
        else:
            raise CachitoError(f"The dependency {package.name}@{package.version} has no source")

        nexus_replacement = None

        non_registry = not package.url or not _is_from_npm_registry(package.url)
        if non_registry:
            log.info("The dependency %r is not from the npm registry", f"{package.name}@{source}")
            # If the file dependency is in the allowlist, do not convert it to a Nexus
            # dependency, simply ignore it (it will also be ignored by the code that downloads
            # dependencies later)
            if package.relpath and normpath(package.relpath) in file_deps_allowlist:
                log.info("The dependency %r is an allowed exception", package.name)
            else:
                nexus_replacement = _convert_to_nexus_hosted(package.name, source, dep_data)

        dep = {
            "bundled": False,  # yarn.lock does not seem to contain bundled deps at all
            "dev": False,  # yarn.lock does not state whether a dependency is dev
            "name": package.name,
            "version_in_nexus": nexus_replacement["version"] if nexus_replacement else None,
            "type": "yarn",
            "version": package.version if not non_registry else source,
        }
        deps.append(dep)

        if nexus_replacement:
            nexus_replacements[dep_identifier] = nexus_replacement

    return deps, nexus_replacements


def _is_from_npm_registry(pkg_url):
    """
    Check if package is from the NPM registry (which is also the Yarn registry).

    :param str pkg_url: url of the package, in yarn.lock this is always the "resolved" key
    :rtype: bool
    """
    return urlparse(pkg_url).hostname in NPM_REGISTRY_CNAMES


def _pick_strongest_crypto_hash(integrity_value):
    """
    Pick the strongest hash in an SSRI integrity value. SHA-512 > SHA-384 > SHA-256 > other.

    See https://w3c.github.io/webappsec-subresource-integrity/#hash-functions

    Example:
    >>> _pick_strongest_crypto_hash("sha1-qwer... sha512-asdf... sha256-zxcv...")
    >>> "sha512-asdf..."

    :param str integrity_value: SSRI integrity value from a yarn.lock file
    :return: the substring with the strongest hash algorithm
    """
    integrities = integrity_value.split()

    def priority(integrity):
        algorithm = integrity.split("-", 1)[0]
        if algorithm == "sha512":
            return 3
        if algorithm == "sha384":
            return 2
        if algorithm == "sha256":
            return 1
        return 0

    return max(integrities, key=priority)


def _convert_to_nexus_hosted(dep_name, dep_source, dep_info):
    """
    Convert the input dependency not from the NPM registry to a Nexus hosted dependency.

    :param str dep_name: the name of the dependency
    :param str dep_source: the source (url or relative path) of the dependency
    :param dict dep_info: the dependency info from the yarn lock file
    :return: the dependency information of the Nexus hosted version to use in the yarn lock file
        instead of the original
    :raise CachitoError: if the dependency is from an unsupported location or has an unexpected
        format in the lock file
    """
    integrity = dep_info.get("integrity")
    if integrity:
        integrity = _pick_strongest_crypto_hash(integrity)
    else:
        # For http(s) non-registry dependencies, yarn does not seem to include the "integrity" key
        # by default. It does, however, include a sha1 hash in the resolved url fragment.
        url = urlparse(dep_source)
        if url.fragment and url.scheme in ("http", "https"):
            integrity = convert_hex_sha_to_npm(url.fragment, "sha1")

    dep = JSDependency(name=dep_name, source=dep_source, integrity=integrity)
    dep_in_nexus = process_non_registry_dependency(dep)

    converted_dep_info = copy.deepcopy(dep_info)
    converted_dep_info.update(
        {
            "integrity": dep_in_nexus.integrity,
            "resolved": dep_in_nexus.source,
            "version": dep_in_nexus.version,
        }
    )
    return converted_dep_info


def _get_package_and_deps(package_json_path, yarn_lock_path):
    """
    Get the main package and dependencies based on the lock file.

    If the lockfile contains non-registry dependencies, the lock file will be modified to use ones
    in Nexus. Non-registry dependencies will have the "version_in_nexus" key set.

    :param (str | Path) package_json_path: the path to the package.json file
    :param (str | Path) yarn_lock_path: the path to the lock file
    :return: a dictionary that has the keys "deps" which is the list of dependencies,
        "lock_file" which is the lock file if it was modified (as a dict!), "package" which is the
        dictionary describing the main package, and "package.json" which is the package.json file if
        it was modified.
    :rtype: dict
    """
    with open(package_json_path) as f:
        package_json = json.load(f)

    yarn_lock = pyarn.lockfile.Lockfile.from_file(str(yarn_lock_path)).data

    try:
        package = {
            "name": package_json["name"],
            "version": package_json["version"],
            "type": "yarn",
        }
    except KeyError:
        raise CachitoError("The package.json file is missing required data (name, version)")

    file_deps_allowlist = set(
        get_worker_config().cachito_yarn_file_deps_allowlist.get(package["name"], [])
    )

    deps, nexus_replacements = _get_deps(yarn_lock, file_deps_allowlist)

    if nexus_replacements:
        package_json_replaced = _replace_deps_in_package_json(package_json, nexus_replacements)
        yarn_lock_replaced = _replace_deps_in_yarn_lock(yarn_lock, nexus_replacements)
    else:
        package_json_replaced = None
        yarn_lock_replaced = None

    return {
        "package": package,
        "deps": deps,
        "package.json": package_json_replaced,
        "lock_file": yarn_lock_replaced,
    }


def _replace_deps_in_package_json(package_json, nexus_replacements):
    """
    Replace non-registry dependencies in package.json with their versions in Nexus.

    :param dict package_json: parsed package.json data
    :param dict nexus_replacements: modified subset of yarn.lock data, a dict in the format:
        {<dependency identifier>: <dependency info>}
    :return: copy of package.json data with replacements applied (or None if no replacements match)
    """
    expanded_replacements = {}

    for deps_identifier, nexus_replacement in nexus_replacements.items():
        # The dependency identifier is a line that may contain multiple comma-separated
        # dependencies (different ways to specify the same dependency)
        deps = map(str.strip, deps_identifier.split(","))
        for d in deps:
            expanded_replacements[d] = nexus_replacement

    package_json_new = copy.deepcopy(package_json)
    modified = False

    for dep_type in ("dependencies", "devDependencies"):
        for dep_name, dep_version in package_json.get(dep_type, {}).items():
            dep_identifier = f"{dep_name}@{dep_version}"

            if dep_identifier not in expanded_replacements:
                continue

            new_version = expanded_replacements[dep_identifier]["version"]
            log.info(
                "Replacing the version of %s in %s from %s to %s in package.json",
                dep_name,
                dep_type,
                dep_version,
                new_version,
            )
            package_json_new[dep_type][dep_name] = new_version
            modified = True

    return package_json_new if modified else None


def _replace_deps_in_yarn_lock(yarn_lock, nexus_replacements):
    """
    Replace non-registry dependencies in yarn.lock with their versions in Nexus.

    :param dict yarn_lock: parsed yarn.lock data
    :param dict nexus_replacements: modified subset of yarn.lock data, a dict in the format:
        {<dependency identifier>: <dependency info>}
    :return: copy of yarn.lock data with replacements applied
    """
    yarn_lock_new = copy.deepcopy(yarn_lock)
    yarn_lock_new.update(copy.deepcopy(nexus_replacements))
    return yarn_lock_new


def resolve_yarn(app_source_path, request, skip_deps=None):
    """
    Resolve and fetch npm dependencies for the given app source archive.

    :param (str | Path) app_source_path: the full path to the application source code
    :param dict request: the Cachito request this is for
    :param set skip_deps: a set of dependency identifiers to not download because they've already
        been downloaded for this request
    :return: a dictionary that has the following keys:
        ``deps`` which is the list of dependencies,
        ``downloaded_deps`` which is a set of the dependency identifiers of the dependencies that
        were downloaded as part of this function's execution,
        ``lock_file`` which is the lock file if it was modified,
        ``package`` which is the dictionary describing the main package, and
        ``package.json`` which is the package.json file if it was modified.
    :rtype: dict
    :raises CachitoError: if fetching the dependencies fails or required files are missing
    """
    app_source_path = Path(app_source_path)

    package_json_path = app_source_path / "package.json"
    yarn_lock_path = app_source_path / "yarn.lock"
    package_and_deps_info = _get_package_and_deps(package_json_path, yarn_lock_path)

    # By downloading the dependencies, it stores the tarballs in the bundle and also stages the
    # content in the npm repository for the request
    proxy_repo_url = get_yarn_proxy_repo_url(request["id"])
    package_and_deps_info["downloaded_deps"] = download_dependencies(
        request["id"],
        package_and_deps_info["deps"],
        proxy_repo_url,
        skip_deps=skip_deps,
        pkg_manager="yarn",
    )

    # Remove all the "bundled" and "version_in_nexus" keys since they are implementation details
    for dep in package_and_deps_info["deps"]:
        dep.pop("bundled")
        dep.pop("version_in_nexus")

    return package_and_deps_info
