# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import copy
import json
import logging
import os

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config
from cachito.workers.pkg_managers.general_js import (
    download_dependencies,
    get_npm_component_info_from_nexus,
    upload_non_registry_dependency,
)

__all__ = [
    "convert_hex_sha512_to_npm",
    "convert_integrity_to_hex_checksum",
    "convert_to_nexus_hosted",
    "get_npm_proxy_repo_name",
    "get_npm_proxy_repo_url",
    "get_npm_proxy_username",
    "get_package_and_deps",
    "resolve_npm",
]

log = logging.getLogger(__name__)


def _get_deps(package_lock_deps, _name_to_deps=None):
    """
    Get a mapping of dependencies to all versions of the dependency.

    This function works by populating the input ``_name_to_deps``. When first called, this value
    will be set to ``{}``. As the function executes, it will call itself by passing in the current
    value of ``_name_to_deps`` for instances where the dependency tree is more than a level deep.
    This occurs when dependency A and B depend on different versions of the dependency C.

    ``_name_to_deps`` is a dictionary so that deduplication is much more efficient than if it were
    a list. If it were a list, there'd be a time complexity of O(n) every time a dependency is to be
    inserted.

    If dependencies not from the NPM registry are encountered and their locations are not
    supported, then a ``CachitoError`` exception will be raised. If the location is supported,
    the input ``package_lock_deps`` will be modifed to use a reference to Nexus instead for that
    dependency.

    :param dict package_lock_deps: the value of a "dependencies" key in a package-lock.json file
    :param dict _name_to_deps: the current mapping of dependencies; this is not meant to be set
        by the caller
    :return: a tuple with the first item as the mapping of dependencies where each key is a
        dependecy name and the values are dictionaries describing the dependency versions; the
        second item is a list of tuples for non-registry dependency replacements, where the first
        item is the dependency name and the second item is the version of the dependency in Nexus
    :rtype: (dict, list)
    :raise CachitoError: if the lock file contains a dependency from an unsupported location
    """
    if _name_to_deps is None:
        _name_to_deps = {}

    nexus_replacements = []
    for name, info in package_lock_deps.items():
        nexus_replacement = None
        version_in_nexus = None
        # Note that a bundled dependency will not have the "resolved" key, but those are supported
        # since they are properly cached in the parent dependency in Nexus
        if not info.get("bundled", False) and "resolved" not in info:
            log.info("The dependency %r is not from the npm registry", info)
            # If the non-registry isn't supported, convert_to_nexus_hosted will raise a
            # CachitoError exception
            nexus_replacement = convert_to_nexus_hosted(name, info)
            version_in_nexus = nexus_replacement["version"]
            nexus_replacements.append((name, version_in_nexus))

        dep = {
            "bundled": info.get("bundled", False),
            "dev": info.get("dev", False),
            "name": name,
            "version_in_nexus": version_in_nexus,
            "type": "npm",
            "version": info["version"],
        }
        if nexus_replacement:
            # Replace the original dependency in the npm-shrinkwrap.json or package-lock.json file
            # with the dependency in the Nexus hosted repo
            info.clear()
            info.update(nexus_replacement)

        _name_to_deps.setdefault(name, [])
        for d in _name_to_deps[name]:
            if d["version"] == dep["version"]:
                # If a duplicate version was found but this one isn't bundled, then mark the
                # dependency as not bundled so it's included individually in the deps directory
                if not dep["bundled"]:
                    d["bundled"] = False
                # If a duplicate version was found but this one isn't a dev dependency, then mark
                # the dependency as not dev
                if not dep["dev"]:
                    d["dev"] = False
                break
        else:
            _name_to_deps[name].append(dep)

        if "dependencies" in info:
            _, returned_nexus_replacements = _get_deps(info["dependencies"], _name_to_deps)
            # If any of the dependencies were non-registry dependencies, replace the requires to be
            # the version in Nexus
            for name, version in returned_nexus_replacements:
                info["requires"][name] = version

    return _name_to_deps, nexus_replacements


def convert_hex_sha512_to_npm(hex_sha512):
    """
    Convert the input sha512 checksum in hex to the format that an npm lock file uses.

    This is useful for converting sha512 checksums from Nexus.

    :param str hex_sha512: the sha512 checksum in hex
    :return: the sha512 checksum in npm package-lock.json file format
    :rtype: str
    """
    bytes_sha512 = bytes.fromhex(hex_sha512)
    base64_sha512 = base64.b64encode(bytes_sha512).decode("utf-8")
    return f"sha512-{base64_sha512}"


def convert_integrity_to_hex_checksum(integrity):
    """
    Convert the input integrity value of a dependency to a hex checksum.

    The integrity is a key in an npm lock file that contains the checksum of the dependency in the
    format of ``<algorithm>-<base64 of the binary hash>``.

    :param str integrity: the integrity from the npm lock file
    :return: a tuple where the first item is the algorithm used and second is the hex value of
        the checksum
    :rtype: (str, str)
    """
    algorithm, checksum = integrity.split("-", 1)
    return algorithm, base64.b64decode(checksum).hex()


def convert_to_nexus_hosted(dep_name, dep_info):
    """
    Convert the input dependency not from the NPM registry to a Nexus hosted dependency.

    :param str dep_name: the name of the dependency
    :param dict dep_info: the dependency info from the npm lock file (e.g. package-lock.json)
    :return: the dependency information of the Nexus hosted version to use in the npm lock file
        instead of the original
    :raise CachitoError: if the dependency is from an unsupported location or has an unexpected
        format in the lock file
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
    # The version value for a dependency outside of the npm registry is the identifier to use for
    # commands such as `npm pack` or `npm install`
    # Examples of version values:
    #   git+https://github.com/ReactiveX/rxjs.git#dfa239d41b97504312fa95e13f4d593d95b49c4b
    #   github:ReactiveX/rxjs#78032157f5c1655436829017bbda787565b48c30
    #   https://github.com/jsplumb/jsplumb/archive/2.10.2.tar.gz
    dep_identifier = dep_info["version"]
    if any(dep_identifier.startswith(prefix) for prefix in git_prefixes):
        try:
            _, commit_hash = dep_identifier.rsplit("#", 1)
        except ValueError:
            msg = (
                f"The dependency {dep_identifier} in the npm lock file was in an unexpected format"
            )
            log.error(msg)
            raise CachitoError(msg)
        # When the dependency is uploaded to the Nexus hosted repository, it will be in the format
        # of `<version>-gitcommit-<commit hash>`
        version_suffix = f"-external-gitcommit-{commit_hash}"
    elif any(dep_identifier.startswith(prefix) for prefix in http_prefixes):
        if "integrity" not in dep_info:
            msg = f"The dependency {dep_identifier} is missing the integrity value in the lock file"
            log.error(msg)
            raise CachitoError(msg)

        algorithm, checksum = convert_integrity_to_hex_checksum(dep_info["integrity"])
        # When the dependency is uploaded to the Nexus hosted repository, it will be in the format
        # of `<version>-external-<checksum algorithm>-<hex checksum>`
        version_suffix = f"-external-{algorithm}-{checksum}"
    else:
        raise CachitoError(f"The dependency {dep_identifier} is hosted in an unsupported location")

    component_info = get_npm_component_info_from_nexus(dep_name, f"*{version_suffix}")
    if not component_info:
        upload_non_registry_dependency(dep_identifier, version_suffix)
        component_info = get_npm_component_info_from_nexus(
            dep_name, f"*{version_suffix}", max_attempts=5
        )
        if not component_info:
            raise CachitoError(
                f"The dependency {dep_identifier} was uploaded to Nexus but is not accessible"
            )

    converted_dep_info = copy.deepcopy(dep_info)
    # The "from" value is the original value from package.json for some locations
    converted_dep_info.pop("from", None)
    converted_dep_info.update(
        {
            "integrity": convert_hex_sha512_to_npm(
                component_info["assets"][0]["checksum"]["sha512"]
            ),
            "resolved": component_info["assets"][0]["downloadUrl"],
            "version": component_info["version"],
        }
    )
    return converted_dep_info


def get_npm_proxy_repo_name(request_id):
    """
    Get the name of npm proxy repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the name of npm proxy repository for the request
    :rtype: str
    """
    config = get_worker_config()
    return f"{config.cachito_nexus_request_repo_prefix}npm-{request_id}"


def get_npm_proxy_repo_url(request_id):
    """
    Get the URL for the Nexus npm proxy repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the URL for the Nexus npm proxy repository for the request
    :rtype: str
    """
    config = get_worker_config()
    repo_name = get_npm_proxy_repo_name(request_id)
    return f"{config.cachito_nexus_url.rstrip('/')}/repository/{repo_name}/"


def get_npm_proxy_username(request_id):
    """
    Get the username that has read access on the npm proxy repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the username
    :rtype: str
    """
    return f"cachito-npm-{request_id}"


def get_package_and_deps(package_json_path, package_lock_path):
    """
    Get the main package and dependencies based on the lock file.

    If the lockfile contains non-registry dependencies, the lock file will be modified to use ones
    in Nexus. Non-registry dependencies will have the "version_in_nexus" key set.

    :param str package_json_path: the path to the package.json file
    :param str package_lock_path: the path to the lock file
    :return: a dictionary that has the keys "deps" which is the list of dependencies,
        "lock_file" which is the lock file if it was modified, "package" which is the
        dictionary describing the main package, and "package.json" which is the package.json file if
        it was modified.
    :rtype: dict
    """
    with open(package_lock_path, "r") as f:
        package_lock = json.load(f)

    package_lock_original = copy.deepcopy(package_lock)
    name_to_deps, top_level_replacements = _get_deps(package_lock.get("dependencies", {}))
    # Convert the name_to_deps mapping to a list now that it's fully populated
    deps = [dep_info for deps_info in name_to_deps.values() for dep_info in deps_info]
    package = {"name": package_lock["name"], "type": "npm", "version": package_lock["version"]}

    rv = {
        "deps": deps,
        "lock_file": None,
        "package": package,
        "package.json": None,
    }

    # If top level replacements are returned, the package.json may need to be updated to use
    # the replaced dependencies in the lock file. If these updates don't occur, running
    # `npm install` causes the lock file to be updated since it's assumed that it's out of date.
    if top_level_replacements:
        with open(package_json_path, "r") as f:
            package_json = json.load(f)

        package_json_original = copy.deepcopy(package_json)
        for dep_name, dep_version in top_level_replacements:
            for dep_type in ("dependencies", "devDependencies"):
                if dep_name in package_json.get(dep_type, {}):
                    log.info(
                        "Replacing the version of %s in %s from %s to %s in package.json",
                        dep_name,
                        dep_type,
                        package_json[dep_type][dep_name],
                        dep_version,
                    )
                    package_json[dep_type][dep_name] = dep_version

        if package_json != package_json_original:
            rv["package.json"] = package_json

    if package_lock != package_lock_original:
        rv["lock_file"] = package_lock

    return rv


def resolve_npm(app_source_path, request):
    """
    Resolve and fetch npm dependencies for the given app source archive.

    :param str app_source_path: the full path to the application source code
    :param dict request: the Cachito request this is for
    :return: a dictionary that has the keys "deps" which is the list of dependencies,
        "lock_file" which is the lock file if it was modified, "package" which is the
        dictionary describing the main package, and "package.json" which is the package.json file if
        it was modified.
    :rtype: dict
    :raises CachitoError: if fetching the dependencies fails or required files are missing
    """
    # npm-shrinkwrap.json and package-lock.json share the same format but serve slightly
    # different purposes. See the following documentation for more information:
    # https://docs.npmjs.com/files/package-lock.json.
    for lock_file in ("npm-shrinkwrap.json", "package-lock.json"):
        package_lock_path = os.path.join(app_source_path, lock_file)
        if os.path.exists(package_lock_path):
            break
    else:
        raise CachitoError(
            "The npm-shrinkwrap.json or package-lock.json file must be present for the npm "
            "package manager"
        )

    package_json_path = os.path.join(app_source_path, "package.json")
    if not os.path.exists(package_json_path):
        raise CachitoError("The package.json file must be present for the npm package manager")

    try:
        package_and_deps_info = get_package_and_deps(package_json_path, package_lock_path)
    except KeyError:
        msg = f"The lock file {lock_file} has an unexpected format"
        log.exception(msg)
        raise CachitoError(msg)

    # By downloading the dependencies, it stores the tarballs in the bundle and also stages the
    # content in the npm repository for the request
    proxy_repo_url = get_npm_proxy_repo_url(request["id"])
    download_dependencies(request["id"], package_and_deps_info["deps"], proxy_repo_url)

    # Remove all the "bundled" keys since that is an implementation detail that should not be
    # exposed outside of this function
    for dep in package_and_deps_info["deps"]:
        dep.pop("bundled")
        dep.pop("version_in_nexus")

    return package_and_deps_info
