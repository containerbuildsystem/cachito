# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional, Self

from cachito.errors import CachitoError, FileAccessError, ValidationError
from cachito.workers.config import get_worker_config
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general_js import (
    JSDependency,
    download_dependencies,
    is_from_npm_registry,
    process_non_registry_dependency,
    vet_file_dependency,
)

__all__ = [
    "get_npm_proxy_repo_name",
    "get_npm_proxy_repo_url",
    "get_npm_proxy_username",
    "get_package_and_deps",
    "resolve_npm",
]

log = logging.getLogger(__name__)


@dataclass
class PackageTreeNode:
    package: Optional["Package"] = None
    parent: Optional["PackageTreeNode"] = None
    children: dict[str, "PackageTreeNode"] = field(default_factory=dict)


class Package:
    """A npm package."""

    def __init__(
        self,
        name: str,
        package_dict: dict[str, Any],
        is_top_level: bool = False,
        alias: Optional[str] = None,
        path: Optional[str] = None,
        dependent_packages: Optional[list["Package"]] = None,
    ) -> None:
        """Initialize a Package.

        :param name: the package name, which should correspond to the name in its package.json
        :param package_dict: the raw dict for a package-lock.json `package` or `dependency`
        :param is_top_level: if this package is in the root node_modules directory
        :param alias: the package name derived from the package path. This may be different from
                      `name` if the package has an aliased name. Set for v2+ lockfiles only.
        :param path: the relative path to the package from the root project dir. This is present
                     for package-lock.json `packages` and not for `dependencies`.
        :param dependent_packages: Packages that depend on this Package. These will be the Packages
                     that have this Package in their `requires` for a v1 package-lock.json.
                     They will be the Packages that have this Package in their `dependencies` for a
                     v2+ package-lock.json.
        """
        self.name = name
        self._package_dict = package_dict
        self.is_top_level = is_top_level
        self.alias = alias
        self.path = path
        self.dependent_packages = dependent_packages or []

    @property
    def version(self) -> str:
        """Get the package version.

        For v1/v2 package-lock.json `dependencies`, this will be a semver
        for registry dependencies and a url for git/https/filepath sources.
        https://docs.npmjs.com/cli/v6/configuring-npm/package-lock-json#dependencies
        For v2+ package-lock.json `packages`, this will be a semver from the package.json file.
        https://docs.npmjs.com/cli/v7/configuring-npm/package-lock-json#packages
        """
        return self._package_dict["version"]

    @version.setter
    def version(self, version: str) -> None:
        """Set the package version."""
        self._package_dict["version"] = version

    @property
    def resolved_url(self) -> str:
        """Get the location where the package was resolved from.

        For v1/v2 package-lock.json `dependencies`, this will be the "resolved"
        key for registry deps and the "version" key for non-registry deps.
        For v2+ package-lock.json `packages`, this will be the "resolved" key
        unless it is a file dep, in which case it will be the path to the file.
        """
        if self.path is not None and "resolved" not in self._package_dict:
            return f"file:{self.path}"

        return self._package_dict.get("resolved") or self.version

    def set_resolved(self, resolved: str) -> None:
        """Set the location where the package was resolved from."""
        self._package_dict["resolved"] = resolved
        # The "from" value is the original value from package.json for some
        # locations. Remove it while setting a new `resolved` location
        self._package_dict.pop("from", None)

    @property
    def bundled(self) -> bool:
        """Return True if this package is bundled."""
        return (
            any(self._package_dict.get(key) for key in ["bundled", "inBundle"])
            # In v2+ lockfiles, direct dependencies do have "inBundle": true if they are to be
            # bundled. They will get bundled if the package is uploaded to the npm registry, but
            # aren't bundled yet. These have a resolved url and shouldn't be considered bundled.
            and "resolved" not in self._package_dict
        )

    @property
    def dev(self) -> bool:
        """Return True if this package is a dev dependency."""
        return self._package_dict.get("dev", False)

    @property
    def is_link(self) -> bool:
        """Return True if this package is a link."""
        return self._package_dict.get("link", False)

    @property
    def integrity(self) -> Optional[str]:
        """Get the package subresource integrity string."""
        return self._package_dict.get("integrity")

    @integrity.setter
    def integrity(self, integrity: str) -> None:
        """Set the package subresource integrity string."""
        self._package_dict["integrity"] = integrity

    @property
    def is_file_dep(self) -> bool:
        """Return True if this package is a file dependency."""
        return self.resolved_url.startswith("file:") and not self.bundled

    @property
    def is_registry_dep(self) -> bool:
        """Return True if this package is a registry dependency."""
        return is_from_npm_registry(self.resolved_url)

    def get_dependency_names(self) -> list[str]:
        """Get the list of names of dependencies that this Package depends on.

        This will be the `dependencies` keys for a given `package` in a
        v2+ package-lock.json file. It will be the `requires` keys for a given
        `dependency` in a v1 package-lock.json file.
        """
        if self.path is None:  # v1 Packages
            return list(self._package_dict.get("requires", {}).keys())

        dep_names: set[str] = set()
        for dep_type in (
            "dependencies",
            "devDependencies",
            "optionalDependencies",
            "peerDependencies",
        ):
            dep_names.update(self._package_dict.get(dep_type, {}).keys())

        return list(dep_names)

    def replace_dependency_version(self, name: str, version: str) -> None:
        """Replace the version of a dependency of this Package.

        For v1 Packages, this will replace the version of the dependency in the
        `requires` dict. For v2 Packages it will do the same in the `dependencies` dict.
        :param name: the name of the dependency that will be updated
        :param version: the updated version of the dependency
        """
        if self.path is None:  # v1 Packages
            if name in self._package_dict.get("requires", {}):
                self._package_dict["requires"][name] = version
        else:
            for dep_type in (
                "dependencies",
                "devDependencies",
                "optionalDependencies",
                "peerDependencies",
            ):
                if name in self._package_dict.get(dep_type, {}):
                    self._package_dict[dep_type][name] = version

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Package):
            return (
                self.name == other.name
                and self.path == other.path
                and self._package_dict == other._package_dict
                and self.is_top_level == other.is_top_level
                and self.alias == other.alias
                and self.dependent_packages == other.dependent_packages
            )
        return False

    def __repr__(self) -> str:
        attr_str = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items())
        return f"{self.__class__.__name__}({attr_str})"


class PackageLock:
    """A npm package-lock.json file."""

    def __init__(self, lockfile_path: Path, lockfile_data: dict[str, Any]) -> None:
        """Initialize a PackageLock."""
        self._lockfile_path = lockfile_path
        self._lockfile_data = lockfile_data
        self._original_lockfile_data = copy.deepcopy(lockfile_data)
        self.packages = (
            self._get_dependencies() if self.lockfile_version == 1 else self._get_packages()
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a copy of the _lockfile_data dict."""
        return copy.deepcopy(self._lockfile_data)

    @property
    def is_modified(self) -> bool:
        """Return True if the lockfile data has been modified since initialization."""
        return self._lockfile_data != self._original_lockfile_data

    @property
    def lockfile_version(self) -> int:
        """Get the lockfileVersion from package-lock.json data."""
        return self._lockfile_data["lockfileVersion"]

    @property
    def main_package(self) -> dict[str, str]:
        """Return a dict with info about the main package."""
        return {
            "name": self._lockfile_data["name"],
            "type": "npm",
            "version": self._lockfile_data["version"],
        }

    @property
    def workspaces(self) -> list[str]:
        """Return a list of the workspaces."""
        return (
            self._lockfile_data["packages"][""].get("workspaces", [])
            if self.lockfile_version >= 2
            else []
        )

    @classmethod
    def from_file(cls, lockfile_path: Path) -> Self:
        """Create a PackageLock from a package-lock.json file."""
        with lockfile_path.open("r") as f:
            lockfile_data = json.load(f)

        lockfile_version = lockfile_data.get("lockfileVersion")
        log.info(f"Processing npm package-lock.json file with lockfileVersion: {lockfile_version}")
        if lockfile_version not in (1, 2, 3):
            raise ValidationError(
                (
                    f"lockfileVersion {lockfile_version} from {lockfile_path} is not supported "
                    "Please use a supported lockfileVersion, which are versions 1, 2, and 3"
                )
            )

        return cls(lockfile_path, lockfile_data)

    def _get_packages(self) -> list[Package]:
        """Return a flat list of Packages from a v2+ package-lock.json file.

        Use the "packages" key in the lockfile to create a list of Package objects.
        """

        def get_package_name_from_path(package_path: str) -> str:
            """Get the package name from the path in v2+ package-lock.json file."""
            path = Path(package_path)
            parent_name = Path(package_path).parents[0].name
            is_scoped = parent_name.startswith("@")
            return (Path(parent_name) / path.name).as_posix() if is_scoped else path.name

        # Create all of the Package objects
        paths_to_packages: dict[str, Package] = {}
        for package_path, package_data in self._lockfile_data.get("packages", {}).items():
            # The path of the package in the lockfile may have a different name than the one in
            # the package data. This could be due to aliasing, etc., so keep both names handy
            package_name = package_data.get("name") or get_package_name_from_path(package_path)
            alias = (
                get_package_name_from_path(package_path)
                if "name" in package_data and package_path != ""
                else None
            )
            paths_to_packages[package_path] = Package(
                package_name, package_data, alias=alias, path=package_path
            )

        # For each Package object, we need to determine all of the Packages that depend on them
        root_node = _get_v2_package_tree(paths_to_packages)
        _resolve_dependent_packages(root_node)
        # Once we've mapped all of the dependent Packages, we no longer need the root Package
        paths_to_packages.pop("", None)

        # Remove link Packages since there is already a Package for the link target
        return [package for package in paths_to_packages.values() if not package.is_link]

    def _get_dependencies(self) -> list[Package]:
        """Return a flat list of Packages from a v1/v2 package-lock.json file.

        Use the "dependencies" key in the lockfile, which can be nested. While
        """
        root_node = PackageTreeNode()

        def get_dependencies_iter(
            dependencies: dict[str, dict[str, Any]],
            root_node: PackageTreeNode,
            parent_node: PackageTreeNode,
        ) -> Iterator[Package]:
            for dependency_name, dependency_data in dependencies.items():
                is_top_level = parent_node == root_node
                dependency = Package(
                    dependency_name, dependency_data, path=None, is_top_level=is_top_level
                )
                yield dependency
                dependency_node = PackageTreeNode(package=dependency, parent=parent_node)
                parent_node.children[dependency_name] = dependency_node
                # v1 lockfiles can have nested dependencies
                if "dependencies" in dependency_data:
                    yield from get_dependencies_iter(
                        dependency_data["dependencies"], root_node, dependency_node
                    )

        packages = list(
            get_dependencies_iter(self._lockfile_data.get("dependencies", {}), root_node, root_node)
        )
        # For each Package object, we need to determine all of the Packages that depend on them
        _resolve_dependent_packages(root_node)

        return packages


def _resolve_dependent_packages(node: PackageTreeNode) -> None:
    """Resolve dependent packages from the given dependency tree.

    Descending from the given PackageTreeNode, resolve the dependencies of each
    Package. Add the dependent Package to the dependent_packages of the resolved
    dependency.

    For example: if package A has a dependency on B, resolve B and add A to B's
    dependent packages. Later if we do a nexus-replacement of B, we know to update
    A to depend on the newly replaced version of B.
    """
    for child_node in node.children.values():
        if child_node.package is None:
            raise CachitoError(
                (
                    "Cachito encountered an error while resolving dependent packages "
                    f"of child node {child_node}, which has no associated Package. "
                    "This should never happen."
                )
            )

        for child_dep_name in child_node.package.get_dependency_names():
            child_dep_pkg = _resolve_node_dependency(child_node, child_dep_name)
            if child_dep_pkg is not None:
                child_dep_pkg.dependent_packages.append(child_node.package)
        _resolve_dependent_packages(child_node)


def _resolve_node_dependency(node: PackageTreeNode, dep_name: str) -> Optional[Package]:
    """Return the Package that the given dep name resolves to relative to this node.

    From the given PackageTreeNode, resolve the dependency. Either resolve it
    from the children of this node or recursively upwards towards the root node.
    If the dependency cannot be resolved, return None.
    """
    if dep_name in node.children:
        return node.children[dep_name].package

    if not node.parent:
        log.warning(
            f"Cachito was unable to resolve dependency {dep_name} in the package tree. "
            "It may be an optional peerDependency that isn't included in package-lock.json"
        )
        return None

    return _resolve_node_dependency(node.parent, dep_name)


def _get_v2_package_tree(paths_to_packages: dict[str, Package]) -> PackageTreeNode:
    """Return the root PackageTreeNode for the `packages` in a v2+ package-lock.json file.

    A v2+ package-lock.json file contains a flat `packages` dict where the keys are the paths
    to a given package. Use the paths to generate a tree structure so that we can later
    determine which packages depend on other packages and do non-registry dependency replacements.
    """
    root_node = PackageTreeNode()
    paths_to_nodes = {
        Path(path): PackageTreeNode(package) for path, package in paths_to_packages.items()
    }

    for path, package_node in paths_to_nodes.items():
        package = package_node.package
        if package is None:
            raise CachitoError(
                (
                    "Cachito encountered an error while constructing the npm package tree. "
                    f"Package node {package_node} has no associated Package. "
                    "This should never happen."
                )
            )
        # links will not be added to the package tree, only link targets
        if package.is_link:
            continue
        parent_node = (
            _get_parent_node(path, root_node, paths_to_nodes)
            if "node_modules" in path.parts
            else _get_fsparent_node(path, root_node, paths_to_nodes)
        )
        if parent_node == root_node:
            package.is_top_level = True
        package_node.parent = parent_node
        parent_node.children[package.alias or package.name] = package_node

    return root_node


def _get_fsparent_node(
    path: Path, root_node: PackageTreeNode, paths_to_nodes: dict[Path, PackageTreeNode]
) -> PackageTreeNode:
    """Return the PackageTreeNode for the Package that is the fsparent for the given path.

    https://github.com/npm/cli/blob/latest/workspaces/arborist/docs/parent.md#fsparent
    """
    if path == Path(""):
        return root_node

    for parent in path.parents:
        if parent == Path(""):
            return root_node
        if parent in paths_to_nodes:
            return paths_to_nodes[parent]

    raise CachitoError(
        (f"Unable to determine the parent package for {path.as_posix()} in the package tree.")
    )


def _get_parent_node(
    path: Path, root_node: PackageTreeNode, paths_to_nodes: dict[Path, PackageTreeNode]
) -> PackageTreeNode:
    """Return the PackageTreeNode for the Package that is the parent for the given path.

    https://github.com/npm/cli/blob/latest/workspaces/arborist/docs/parent.md#parent
    """
    if path == Path(""):
        return root_node

    for parent in path.parents:
        if parent.parent == Path(""):
            return root_node

        if parent.name == "node_modules":
            parent_pkg_path = parent.parent
            if parent_pkg_path.parent.name.startswith("@"):
                parent_pkg_path = Path(parent_pkg_path.parent) / parent_pkg_path.name
            package = paths_to_nodes[parent_pkg_path].package
            if package is None:
                raise CachitoError(
                    (
                        "Cachito encountered an error while determining the parent node "
                        f"of the path {path}. Node {paths_to_nodes[parent_pkg_path]} has no "
                        "associated Package. This should never happen."
                    )
                )
            if package.is_link:
                package = paths_to_nodes[Path(package.resolved_url)].package
            return paths_to_nodes[Path(package.path)]  # type: ignore

    raise CachitoError(
        (f"Unable to determine the parent package for {path.as_posix()} in the package tree.")
    )


def _get_deps(
    package_lock: PackageLock,
    file_deps_allowlist: set[str],
) -> tuple[dict[str, list[Any]], list[tuple[str, str]]]:
    """
    Get a mapping of dependencies to all versions of the dependency.

    ``_name_to_deps`` is a dictionary so that deduplication is much more efficient than if it were
    a list. If it were a list, there'd be a time complexity of O(n) every time a dependency is to be
    inserted.

    If dependencies not from the NPM registry are encountered and their locations are not
    supported, then an exception will be raised. If the location is supported, the Package
    will be modifed to with a reference to the Nexus-hosted dependency instead.

    :param package_lock: the PackageLock object corresponding to the package-lock.json file
    :param file_deps_allowlist: an allow list of dependencies that are allowed to be "file"
        dependencies and should be ignored since they are implementation details
    :return: a tuple with the first item as the mapping of dependencies where each key is a
        dependecy name and the values are dictionaries describing the dependency versions; the
        second item is a list of tuples for non-registry dependency replacements, where the first
        item is the dependency name and the second item is the version of the dependency in Nexus
    :raise InvalidFileFormat: if the dependency has an unexpected format
    :raise UnsupportedFeature: if the dependency is from an unsupported location
    :raise FileAccessError: if the dependency cannot be accessed
    """
    name_to_deps: dict[str, list[dict[str, Any]]] = {}
    top_level_replacements: list[tuple[str, str]] = []

    for dependency in package_lock.packages:
        dep = {
            "bundled": dependency.bundled,
            "dev": dependency.dev,
            "name": dependency.name,
            "version_in_nexus": None,
            "type": "npm",
            "version": dependency.version
            if dependency.is_registry_dep or dependency.bundled
            else dependency.resolved_url,
        }

        if dependency.is_file_dep:
            js_dep = JSDependency(name=dependency.name, source=dependency.resolved_url)
            vet_file_dependency(js_dep, package_lock.workspaces, file_deps_allowlist)
        # Note that a bundled dependency will not have the "resolved" key, but those are supported
        # since they are properly cached in the parent dependency in Nexus
        elif not dependency.bundled and not dependency.is_registry_dep:
            log.info(
                (
                    f"The dependency {dependency.name} from {dependency.resolved_url} "
                    "is not from the npm registry"
                )
            )
            # If the non-registry isn't supported, _convert_to_nexus_hosted will raise an exception
            _convert_to_nexus_hosted(dependency)
            dep["version_in_nexus"] = dependency.version

            # If the dependency has an aliased name we want to use that rather than the actual
            # package name when replacing deps in package.json and in any dependent Packages
            if dependency.is_top_level:
                top_level_replacements.append(
                    (dependency.alias or dependency.name, dependency.version)
                )

        name_to_deps.setdefault(dependency.name, [])
        for d in name_to_deps[dependency.name]:
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
            name_to_deps[dependency.name].append(dep)

    return name_to_deps, top_level_replacements


def _convert_to_nexus_hosted(dependency: Package) -> None:
    """
    Convert the input dependency not from the NPM registry to a Nexus hosted dependency.

    :param Package dependency: the non-registry dependency to be converted to nexus hosted
    :raise InvalidFileFormat: if the dependency has an unexpected format
    :raise UnsupportedFeature: if the dependency is from an unsupported location
    :raise FileAccessError: if the dependency cannot be accessed
    """
    # The version value for a dependency outside of the npm registry is the identifier to use for
    # commands such as `npm pack` or `npm install`
    # Examples of version values:
    #   git+https://github.com/ReactiveX/rxjs.git#dfa239d41b97504312fa95e13f4d593d95b49c4b
    #   github:ReactiveX/rxjs#78032157f5c1655436829017bbda787565b48c30
    #   https://github.com/jsplumb/jsplumb/archive/2.10.2.tar.gz

    dep = JSDependency(
        name=dependency.name, source=dependency.resolved_url, integrity=dependency.integrity
    )
    dep_in_nexus = process_non_registry_dependency(dep)

    # Update all Packages that depend on this Package to depend on the nexus-replaced version
    for dependent in dependency.dependent_packages:
        log.debug(
            (
                f"Updating {dependent.name} from {dependency.version} to depend on "
                f"nexus-hosted dependency {dependency.name}@{dep_in_nexus.version}"
            )
        )
        dependent.replace_dependency_version(
            dependency.alias or dependency.name, dep_in_nexus.version
        )

    # Replace the original dependency in the npm-shrinkwrap.json or package-lock.json file
    # with the dependency in the Nexus hosted repo
    dependency.version = dep_in_nexus.version
    dependency.integrity = dep_in_nexus.integrity
    dependency.set_resolved(dep_in_nexus.source)


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
    package_lock = PackageLock.from_file(Path(package_lock_path))
    package = package_lock.main_package

    file_deps_allowlist = set(
        get_worker_config().cachito_npm_file_deps_allowlist.get(package["name"], [])
    )
    name_to_deps, top_level_replacements = _get_deps(package_lock, file_deps_allowlist)
    # Convert the name_to_deps mapping to a list now that it's fully populated
    deps = [dep_info for deps_info in name_to_deps.values() for dep_info in deps_info]

    rv = {"deps": deps, "lock_file": None, "package": package, "package.json": None}

    # If top level replacements are returned, the package.json may need to be updated to use
    # the replaced dependencies in the lock file. If these updates don't occur, running
    # `npm install` causes the lock file to be updated since it's assumed that it's out of date.
    if top_level_replacements:
        with Path(package_json_path).open("r") as f:
            package_json = json.load(f)

        package_json_original = copy.deepcopy(package_json)
        for dep_name, dep_version in top_level_replacements:
            for dep_type in (
                "dependencies",
                "devDependencies",
                "optionalDependencies",
                "peerDependencies",
            ):
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

    if package_lock.is_modified:
        rv["lock_file"] = package_lock.to_dict()

    return rv


def resolve_npm(app_source_path, request, skip_deps=None):
    """
    Resolve and fetch npm dependencies for the given app source archive.

    :param str app_source_path: the full path to the application source code
    :param dict request: the Cachito request this is for
    :param set skip_deps: a set of dependency identifiers to not download because they've already
        been downloaded for this request
    :return: a dictionary that has the following keys:
        ``deps`` which is the list of dependencies,
        ``downloaded_deps`` which is a set of the dependency identifiers of the dependencies that
        were downloaded as part of this function's execution,
        ``lock_file`` which is the lock file if it was modified,
        ``lock_file_name`` is the name of the lock file that was used,
        ``package`` which is the dictionary describing the main package, and
        ``package.json`` which is the package.json file if it was modified.
    :rtype: dict
    :raises FileAccessError: if fetching the dependencies fails or required files are missing
    :raises ValidationError: if lock file does not have the correct format
    """
    # npm-shrinkwrap.json and package-lock.json share the same format but serve slightly
    # different purposes. See the following documentation for more information:
    # https://docs.npmjs.com/files/package-lock.json.
    for lock_file in ("npm-shrinkwrap.json", "package-lock.json"):
        package_lock_path = os.path.join(app_source_path, lock_file)
        if os.path.exists(package_lock_path):
            break
    else:
        raise FileAccessError(
            "The npm-shrinkwrap.json or package-lock.json file must be present for the npm "
            "package manager"
        )

    package_json_path = os.path.join(app_source_path, "package.json")
    if not os.path.exists(package_json_path):
        raise FileAccessError("The package.json file must be present for the npm package manager")

    try:
        package_and_deps_info = get_package_and_deps(package_json_path, package_lock_path)
    except KeyError as e:
        msg = f"The lock file {lock_file} has an unexpected format (missing key: {e})"
        log.exception(msg)
        raise ValidationError(msg)

    package_and_deps_info["lock_file_name"] = lock_file
    # By downloading the dependencies, it stores the tarballs in the bundle and also stages the
    # content in the npm repository for the request
    proxy_repo_url = get_npm_proxy_repo_url(request["id"])
    bundle_dir = RequestBundleDir(request["id"])
    bundle_dir.npm_deps_dir.mkdir(exist_ok=True)
    package_and_deps_info["downloaded_deps"] = download_dependencies(
        bundle_dir.npm_deps_dir,
        package_and_deps_info["deps"],
        proxy_repo_url,
        skip_deps,
    )

    # Remove all the "bundled" keys since that is an implementation detail that should not be
    # exposed outside of this function
    for dep in package_and_deps_info["deps"]:
        dep.pop("bundled")
        dep.pop("version_in_nexus")

    return package_and_deps_info
