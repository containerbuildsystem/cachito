import copy
import json
import logging
from collections import deque
from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional
from urllib.parse import urlparse

import pyarn.lockfile

from cachito.errors import InvalidRepoStructure, InvalidRequestData, NexusError
from cachito.workers.config import get_worker_config
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general_js import (
    JSDependency,
    convert_hex_sha_to_npm,
    download_dependencies,
    get_yarn_component_info_from_non_hosted_nexus,
    process_non_registry_dependency,
    vet_file_dependency,
)

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


class Workspace(NamedTuple):
    """Info about a workspace.

    path: the relative path to this workspace (from the package that contains it)
    glob: the pattern that matched this workspace path
    package_json: the parsed package.json for this workspace
    """

    path: Path
    glob: str
    package_json: dict[str, Any]

    @property
    def name(self) -> str:
        return self.package_json["name"]

    @property
    def file_version(self) -> str:
        return f"file:{self.path.as_posix()}"


def _get_yarn_workspaces(package_path: Path, package_json: dict[str, Any]) -> list[Workspace]:
    workspaces_attr = package_json.get("workspaces", [])
    if isinstance(workspaces_attr, list):
        # "workspaces": ["packages/*"]
        workspace_globs = workspaces_attr
    else:
        # "workspaces": {
        #   "packages": ["packages/*"],
        #   "nohoist": ["**/react-native", "**/react-native/**"]
        # }
        # https://classic.yarnpkg.com/blog/2018/02/15/nohoist/
        workspace_globs = workspaces_attr.get("packages", [])

    def relpath(abspath: Path) -> Path:
        return abspath.relative_to(package_path)

    workspaces = []

    for workspace_glob in workspace_globs:
        for workspace_path in sorted(package_path.glob(workspace_glob)):
            workspace_json = workspace_path / "package.json"
            if not workspace_json.exists():
                continue

            # https://classic.yarnpkg.com/lang/en/docs/workspaces/#toc-limitations-caveats
            #   "Workspaces must be descendants of the workspace root"
            #   (we re-check to protect against package.jsons outside the user's repository)
            if not workspace_json.resolve().is_relative_to(package_path):
                raise InvalidRepoStructure(
                    f"Workspace path leads outside the package root: {relpath(workspace_json)}"
                )

            log.debug("Found a yarn workspace at %s", relpath(workspace_json))
            workspaces.append(
                Workspace(
                    path=relpath(workspace_path),
                    glob=workspace_glob,
                    package_json=json.loads(workspace_json.read_text()),
                )
            )

    return workspaces


def _find_non_dev_deps(
    main_package_json: dict[str, Any], yarn_lock: dict[str, Any], workspaces: list[Workspace]
) -> set[str]:
    """Find all the non-dev dependencies of a package.

    A dependency is non-dev if:
        * it's in `dependencies`, `peerDependencies` or `optionalDependencies`
          (in the main package.json or the package.json of any workspace)
        * it's the dependency of a non-dev dependency
    """
    non_dev_deps: set[str] = set()

    all_package_jsons = (main_package_json, *(ws.package_json for ws in workspaces))
    root_dep_ids = [
        f"{name}@{version}"
        for dep_type in ["dependencies", "peerDependencies", "optionalDependencies"]
        for package_json in all_package_jsons
        for name, version in package_json.get(dep_type, {}).items()
    ]

    expanded_yarn_lock = _expand_yarn_lock_keys(yarn_lock)

    for dep_id in root_dep_ids:
        if dep_id not in non_dev_deps:
            _add_reachable_deps(dep_id, expanded_yarn_lock, non_dev_deps)

    return non_dev_deps


def _add_reachable_deps(
    dep_id: str, expanded_yarn_lock: dict[str, Any], visited_deps: set[str]
) -> None:
    """
    Add all dependencies reachable from a top-level dependency to the set of visited dependencies.

    :param dep_id: the name@version ID of a top-level dependency in package.json
    :param expanded_yarn_lock: parsed yarn.lock file with expanded keys (see _expand_yarn_lock_keys)
    :param visited_deps: set of already visited non-dev dependencies
    """
    bfs_queue = deque([dep_id])

    while bfs_queue:
        current_dep = bfs_queue.popleft()
        visited_deps.add(current_dep)

        # Note: yarn.lock does not include all dependencies!
        #   Specifically, dependencies that resolve to a workspace may not show up at all.
        #   When we encounter such a dependency, we stop searching the dependency tree.
        if dep_info := expanded_yarn_lock.get(current_dep):
            package = pyarn.lockfile.Package.from_dict(current_dep, dep_info)
            bfs_queue.extend(
                f"{name}@{version}"
                for name, version in package.dependencies.items()
                if f"{name}@{version}" not in visited_deps
            )


def _split_yarn_lock_key(dep_identifer):
    """
    Remove unnecessary quotes in dep_identifier and split the string into a list of dependencies.

    String dep_identifer contains one or more dependencies separated by commas.

    :param dep_identifier: a string which lists all of the dependencies in the identifer
    :return: a list of all the dependencies in the identifier
    """
    return dep_identifer.replace('"', "").split(", ")


def _get_deps(
    package_json: dict[str, Any],
    yarn_lock: dict[str, Any],
    file_deps_allowlist: set[str],
    workspaces: list[Workspace],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Process the dependencies in a yarn.lock file and return relevant information.

    Will convert non-registry dependencies to Nexus hosted dependencies. If there are any
    non-registry dependencies, the yarn.lock file should later be modified to point to the
    replacement Nexus dependencies. This function returns all the information necessary to
    do that.

    The list of dependencies returned by this function is in a format that can be used directly
    as input to the general_js.download_dependencies function.

    :param package_json: parsed package.json data
    :param yarn_lock: parsed yarn.lock data
    :param file_deps_allowlist: an allow list of dependencies that are allowed to be "file"
        dependencies and should be ignored since they are implementation details
    :return: information about preprocessed dependencies and Nexus replacements
    :raise InvalidRequestData: if the lock file contains a dependency from an unsupported location
    """
    deps_by_id = {}
    nexus_replacements = {}
    non_dev_deps = _find_non_dev_deps(package_json, yarn_lock, workspaces)

    for dep_key, dep_data in yarn_lock.items():
        package = pyarn.lockfile.Package.from_dict(dep_key, dep_data)
        if package.url:
            source = package.url
        elif package.relpath:
            source = f"file:{Path(package.relpath).as_posix()}"
        else:
            raise InvalidRequestData(
                f"The dependency {package.name}@{package.version} has no source"
            )

        nexus_replacement = None

        non_registry = not package.url or not _is_from_npm_registry(package.url)
        if non_registry:
            if source.startswith("file:"):
                js_dep = JSDependency(package.name, source)
                vet_file_dependency(js_dep, [ws.glob for ws in workspaces], file_deps_allowlist)
            else:
                log.info(
                    "The dependency %s is not from the npm registry", f"{package.name}@{source}"
                )
                nexus_replacement = _convert_to_nexus_hosted(package.name, source, dep_data)

        version = package.version if not non_registry else source
        canonical_dep_id = f"{package.name}@{version}"

        deps_by_id[canonical_dep_id] = {
            "bundled": False,  # yarn.lock does not seem to contain bundled deps at all
            "dev": not any(dep_id in non_dev_deps for dep_id in _split_yarn_lock_key(dep_key)),
            "name": package.name,
            "version_in_nexus": nexus_replacement["version"] if nexus_replacement else None,
            "type": "yarn",
            "version": version,
        }

        if nexus_replacement:
            nexus_replacements[dep_key] = nexus_replacement

    # make sure workspaces are reported regardless of whether they were present in yarn.lock
    for ws in workspaces:
        ws_id = f"{ws.name}@{ws.file_version}"
        deps_by_id[ws_id] = {
            "bundled": False,
            "dev": False,
            "name": ws.name,
            "version_in_nexus": None,
            "type": "yarn",
            "version": ws.file_version,
        }

    return list(deps_by_id.values()), nexus_replacements


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
    :return: a dict with the "version" and "integrity" keys to replace in the lock file
    :raise InvalidFileFormat: if the dependency has an unexpected format
    :raise UnsupportedFeature: if the dependency is from an unsupported location
    :raise FileAccessError: if the dependency cannot be accessed
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

    return {
        "integrity": dep_in_nexus.integrity,
        # "resolved": this value must be filled in later, after Cachito downloads the dependencies
        "version": dep_in_nexus.version,
    }


def _get_package_and_deps(package_path: Path) -> dict[str, Any]:
    """
    Get the main package and dependencies based on the lock file.

    If the lockfile contains non-registry dependencies, the lock file will be modified to use ones
    in Nexus. Non-registry dependencies will have the "version_in_nexus" key set.

    :param package_path: the path to the package directory
    :return: a dictionary that has the following keys:
        "package": the dictionary describing the main package
        "deps": the list of dependencies
        "package.json": the parsed package.json file (as a dict)
        "lock_file": the parsed yarn.lock file (as a dict)
        "nexus_replacements": dict of replaced external dependencies
    :raises InvalidRequestData: if the package.json file is missing required data
    """
    with package_path.joinpath("package.json").open() as f:
        package_json = json.load(f)

    yarn_lock = pyarn.lockfile.Lockfile.from_file(str(package_path / "yarn.lock")).data

    workspaces = _get_yarn_workspaces(package_path, package_json)

    try:
        package = {
            "name": package_json["name"],
            "version": package_json["version"],
            "type": "yarn",
        }
    except KeyError:
        raise InvalidRequestData("The package.json file is missing required data (name, version)")

    file_deps_allowlist = set(
        get_worker_config().cachito_yarn_file_deps_allowlist.get(package["name"], [])
    )

    deps, nexus_replacements = _get_deps(package_json, yarn_lock, file_deps_allowlist, workspaces)
    return {
        "package": package,
        "deps": deps,
        "package.json": package_json,
        "lock_file": yarn_lock,
        "nexus_replacements": nexus_replacements,
    }


def _set_proxy_resolved_urls(yarn_lock: Dict[str, dict], proxy_repo_name: str) -> bool:
    """
    Set the "resolved" urls for all dependencies, make them point to the proxy repo.

    This must be called *after* Cachito downloads the dependencies, before that they do not yet
    exist in the cachito-yarn-{request_id} proxy repo.

    External dependencies in yarn.lock must be replaced *before* calling this function, see
    _replace_deps_in_yarn_lock.

    :param dict yarn_lock: parsed yarn.lock data with nexus replacements already applied
    :param str proxy_repo_name: the proxy repo name, cachito-yarn-{request_id}
    :return: bool, was anything in the yarn.lock data modified?
    :raises NexusError: if dependency is not available in Nexus proxy repository
    """
    modified = False

    for dep_identifier, dep_data in yarn_lock.items():
        pkg = pyarn.lockfile.Package.from_dict(dep_identifier, dep_data)
        if not pkg.url:
            # Local dependency, does not have a resolved url (and does not need one)
            continue

        pkg_name = pkg.name
        pkg_version = dep_data["version"]

        component_info = get_yarn_component_info_from_non_hosted_nexus(
            pkg_name, pkg_version, proxy_repo_name, max_attempts=5
        )
        if not component_info:
            raise NexusError(
                f"The dependency {pkg_name}@{pkg_version} was uploaded to the Nexus hosted "
                f"repository but is not available in {proxy_repo_name}"
            )

        dep_data["resolved"] = component_info["assets"][0]["downloadUrl"]
        modified = True

    return modified


def _expand_yarn_lock_keys(nexus_replacements: Dict[str, dict]) -> Dict[str, dict]:
    """
    Expand all N:1 keys in the yarn.lock dict into N 1:1 keys.

    In the original dict, 1 key may in fact be N comma-separated keys. These N keys all have the
    same value, making them N:1 keys. In the expanded dict, these will be turned into N 1:1 keys.

    Does not make copies of the original values => when an N:1 key is split, the N new keys will
    all point to the same object (which is also the same object as the original value).

    :param dict nexus_replacements: a dict of nexus replacements which may contain N:1 keys
    :return: a dict of nexus replacements where all N:1 keys have been expanded to N 1:1 keys
    """
    expanded_yarn_lock_keys = {
        key: nexus_replacements[multi_key]
        for multi_key in nexus_replacements
        for key in _split_yarn_lock_key(multi_key)
    }
    return expanded_yarn_lock_keys


def _match_to_new_version(
    dep_name: str, dep_version: str, expanded_replacements: Dict[str, dict]
) -> Optional[str]:
    """
    Match the name and version of a dependency to the new version in an expanded replacements dict.

    :param str dep_name: dependency name
    :param str dep_version: dependency version
    :param dict expanded_replacements: expanded dict of Nexus replacements,
        see _expand_yarn_lock_keys
    :return: new version (str) or None
    """
    dep_identifier = f"{dep_name}@{dep_version}"
    return expanded_replacements.get(dep_identifier, {}).get("version")


def _replace_deps_in_package_json(package_json, nexus_replacements):
    """
    Replace non-registry dependencies in package.json with their versions in Nexus.

    :param dict package_json: parsed package.json data
    :param dict nexus_replacements: modified subset of yarn.lock data, a dict in the format:
        {<dependency identifier>: <dependency info>}
    :return: copy of package.json data with replacements applied (or None if no replacements match)
    """
    expanded_replacements = _expand_yarn_lock_keys(nexus_replacements)

    package_json_new = copy.deepcopy(package_json)
    modified = False

    for dep_type in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        for dep_name, dep_version in package_json.get(dep_type, {}).items():
            new_version = _match_to_new_version(dep_name, dep_version, expanded_replacements)
            if not new_version:
                continue

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

    This function must also replace *top level keys* in the lockfile, not just their values.
    The new key has to be {name}@{version_in_nexus}. Reason being that yarn matches the name and
    version from package.json to the {name}@{version} key in the lockfile. We update the versions
    in package.json => we must also update the keys in yarn.lock.

    :param dict yarn_lock: parsed yarn.lock data
    :param dict nexus_replacements: modified subset of yarn.lock data, a dict in the format:
        {<dependency identifier>: <dependency info>}
    :return: copy of yarn.lock data with replacements applied
    """
    expanded_replacements = _expand_yarn_lock_keys(nexus_replacements)
    yarn_lock_new = {}

    for key, value in yarn_lock.items():
        new_key = key
        new_value = copy.deepcopy(value)

        # The top level keys match the non-expanded replacements
        replacement = nexus_replacements.get(key)
        if replacement:
            pkg_name = pyarn.lockfile.Package.from_dict(key, value).name
            new_key = f"{pkg_name}@{replacement['version']}"
            new_value.update(replacement)

        for dep_name, dep_version in new_value.get("dependencies", {}).items():
            # The values in "dependencies" match the expanded replacements
            new_version = _match_to_new_version(dep_name, dep_version, expanded_replacements)
            if new_version:
                new_value["dependencies"][dep_name] = new_version

        yarn_lock_new[new_key] = new_value

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
    :raises InvalidRequestData: if file is missing from required data
    :raises NexusError: if fetching the dependencies fails or required files are missing
    """
    app_source_path = Path(app_source_path)
    package_and_deps_info = _get_package_and_deps(app_source_path)

    # By downloading the dependencies, it stores the tarballs in the bundle and also stages the
    # content in the yarn repository for the request
    proxy_repo_url = get_yarn_proxy_repo_url(request["id"])
    bundle_dir = RequestBundleDir(request["id"])
    bundle_dir.yarn_deps_dir.mkdir(exist_ok=True)
    package_and_deps_info["downloaded_deps"] = download_dependencies(
        bundle_dir.yarn_deps_dir,
        package_and_deps_info["deps"],
        proxy_repo_url,
        skip_deps=skip_deps,
        pkg_manager="yarn",
    )

    replacements = package_and_deps_info.pop("nexus_replacements")
    pkg_json = _replace_deps_in_package_json(package_and_deps_info["package.json"], replacements)
    yarn_lock = _replace_deps_in_yarn_lock(package_and_deps_info["lock_file"], replacements)

    package_and_deps_info["package.json"] = pkg_json
    if _set_proxy_resolved_urls(yarn_lock, get_yarn_proxy_repo_name(request["id"])):
        package_and_deps_info["lock_file"] = yarn_lock
    else:
        package_and_deps_info["lock_file"] = None

    # Remove all the "bundled" and "version_in_nexus" keys since they are implementation details
    for dep in package_and_deps_info["deps"]:
        dep.pop("bundled")
        dep.pop("version_in_nexus")

    return package_and_deps_info
