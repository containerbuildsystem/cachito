# SPDX-License-Identifier: GPL-3.0-or-later
import json
import logging
import os

from cachito.errors import CachitoError
from cachito.workers.pkg_managers.general_js import download_dependencies

__all__ = ["get_package_and_deps", "resolve_npm"]

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

    :param dict package_lock_deps: the value of a "dependencies" key in a package-lock.json file
    :param dict _name_to_deps: the current mapping of dependencies; this is not meant to be set
        by the caller
    :return: the mapping of dependencies where each key is a dependecy name and the values are
        dictionaries describing the dependency versions
    :rtype: dict
    :raise CachitoError: if the lock file contains a dependency not from the registry
    """
    if _name_to_deps is None:
        _name_to_deps = {}

    for name, info in package_lock_deps.items():
        # Note that if a dependency has bundled dependencies, they will not have the "resolved" key
        # set. This is okay since they are included in the dependency that bundled them. This will
        # be marked below so that any function that uses the results of this function will know
        # not to download these dependencies explicitly.
        if not info.get("bundled", False) and "resolved" not in info:
            log.error("The dependency %r is not from the npm registry", info)
            raise CachitoError(
                "The lock file contains a dependency not from the npm registry. "
                "This is not yet supported."
            )

        dep = {
            "bundled": info.get("bundled", False),
            "dev": info.get("dev", False),
            "name": name,
            "type": "npm",
            "version": info["version"],
        }
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
            _get_deps(info["dependencies"], _name_to_deps)

    return _name_to_deps


def get_package_and_deps(package_lock_path):
    """
    Get the main package and dependencies based on the lock file.

    :param str package_lock_path: the path to the lock file
    :return: the dictionary of the main package and the list of dependencies
    :rtype: (dict, list)
    """
    with open(package_lock_path, "r") as f:
        package_lock = json.load(f)

    name_to_deps = _get_deps(package_lock.get("dependencies", {}))
    # Convert the name_to_deps mapping to a list now that it's fully populated
    deps = [dep_info for deps_info in name_to_deps.values() for dep_info in deps_info]
    package = {"name": package_lock["name"], "type": "npm", "version": package_lock["version"]}

    return package, deps


def resolve_npm(app_source_path, request):
    """
    Resolve and fetch npm dependencies for the given app source archive.

    :param str app_source_path: the full path to the application source code
    :param dict request: the Cachito request this is for
    :return: a tuple of the npm package itself and the list of dictionaries representing the
        dependencies
    :rtype: (dict, list)
    :raises CachitoError: if fetching the dependencies fails
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

    try:
        package, deps = get_package_and_deps(package_lock_path)
    except KeyError:
        msg = f"The lock file {lock_file} has an unexpected format"
        log.exception(msg)
        raise CachitoError(msg)
    # By downloading the dependencies, it stores the tarballs in the bundle and also stages the
    # content in the npm repository for the request
    download_dependencies(request["id"], deps)

    # Remove all the "bundled" keys since that is an implementation detail that should not be
    # exposed outside of this function
    for dep in deps:
        dep.pop("bundled")

    return package, deps
