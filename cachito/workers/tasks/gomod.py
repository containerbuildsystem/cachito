# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
from pathlib import Path

from cachito.common.packages_data import PackagesData
from cachito.errors import (
    FileAccessError,
    GoModError,
    InvalidRepoStructure,
    InvalidRequestData,
    UnsupportedFeature,
)
from cachito.workers.config import get_worker_config
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general import update_request_env_vars
from cachito.workers.pkg_managers.gomod import (
    match_parent_module,
    path_to_subpackage,
    resolve_gomod,
)
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.utils import get_request, runs_if_request_in_progress, set_request_state

__all__ = ["fetch_gomod_source"]
log = logging.getLogger(__name__)


def _find_missing_gomod_files(bundle_dir, subpaths):
    """
    Find all go modules with missing gomod files.

    These files will need to be present in order for the package manager to proceed with
    fetching the package sources.

    :param RequestBundleDir bundle_dir: the ``RequestBundleDir`` object for the request
    :param list subpaths: a list of subpaths in the source repository of gomod packages
    :return: a list containing all non-existing go.mod files across subpaths
    :rtype: list
    """
    invalid_gomod_files = []
    for subpath in subpaths:
        bundle_dir_subpath = bundle_dir.app_subpath(subpath)
        package_gomod_rel_path = bundle_dir_subpath.relpath(bundle_dir_subpath.go_mod_file)
        log.debug("Testing for go mod file in {}".format(package_gomod_rel_path))
        if not bundle_dir_subpath.go_mod_file.exists():
            invalid_gomod_files.append(package_gomod_rel_path)

    return invalid_gomod_files


def _is_workspace(repo_root: Path, subpath: str):
    current_path = repo_root / subpath

    while current_path != repo_root:
        if (current_path / "go.work").exists():
            log.warning("go.work file found at %s", current_path)
            return True
        current_path = current_path.parent

    if (repo_root / "go.work").exists():
        log.warning("go.work file found at %s", repo_root)
        return True

    return False


def _fail_if_bundle_dir_has_workspaces(bundle_dir: RequestBundleDir, subpaths: list[str]):
    for subpath in subpaths:
        if _is_workspace(bundle_dir.source_root_dir, subpath):
            raise InvalidRepoStructure("Go workspaces are not supported by Cachito.")


def _fail_if_parent_replacement_not_included(packages_json_data: PackagesData) -> None:
    """
    Fail if any dependency replacement refers to a parent dir that isn't included in this request.

    :param PackagesData packages_json_data: the collection of resolved packages for the request
    :raises RuntimeError: if there is no parent Go module for the package being processed
    :raises InvalidRequestData: if the module being replaced is not part of this request
    """
    modules = [
        package["name"] for package in packages_json_data.packages if package["type"] == "gomod"
    ]

    for package in packages_json_data.packages:
        for dependency in package.get("dependencies", []):
            if dependency["version"] and ".." in Path(dependency["version"]).parts:
                pkg_module_name = match_parent_module(package["name"], modules)
                if pkg_module_name is None:
                    # This should be impossible
                    raise RuntimeError(
                        f"Could not find parent Go module for package: {package['name']}"
                    )

                dep_name = dependency["name"]
                dep_normpath = os.path.normpath(
                    os.path.join(pkg_module_name, dependency["version"])
                )
                dep_module_name = match_parent_module(dep_name, modules) or match_parent_module(
                    dep_normpath, modules
                )

                if not dep_module_name:
                    raise InvalidRequestData(
                        (
                            f"Could not find a Go module in this request containing "
                            f"{dependency['name']} while processing dependency {dependency} "
                            f"of package {package['name']}. Please tell Cachito to process "
                            f"the module which contains the dependency. Perhaps the parent "
                            f"module of {pkg_module_name}?"
                        )
                    )


@app.task
@runs_if_request_in_progress
def fetch_gomod_source(request_id, dep_replacements=None, package_configs=None):
    """
    Resolve and fetch gomod dependencies for a given request.

    :param int request_id: the Cachito request ID this is for
    :param list dep_replacements: dependency replacements with the keys "name" and "version"; only
        supported with a single path
    :param list package_configs: the list of optional package configurations submitted by the user
    :raises FileAccessError: if a file is not present for the gomod package manager
    :raises UnsupportedFeature: if dependency replacements are provided for
        a non-single go module path
    :raises GoModError: if failed to fetch gomod dependencies
    """
    config = get_worker_config()
    if package_configs is None:
        package_configs = []

    bundle_dir: RequestBundleDir = RequestBundleDir(request_id)
    subpaths = [os.path.normpath(c["path"]) for c in package_configs if c.get("path")]

    if not subpaths:
        # Default to the root of the application source
        subpaths = [os.curdir]

    _fail_if_bundle_dir_has_workspaces(bundle_dir, subpaths)

    invalid_gomod_files = _find_missing_gomod_files(bundle_dir, subpaths)
    if invalid_gomod_files:
        invalid_files_print = "; ".join(invalid_gomod_files)
        file_suffix = "s" if len(invalid_gomod_files) > 1 else ""

        # missing gomod files is supported if there is only one path referenced
        if config.cachito_gomod_ignore_missing_gomod_file and len(subpaths) == 1:
            log.warning("go.mod file missing for request at %s", invalid_files_print)
            return

        raise FileAccessError(
            "The {} file{} must be present for the gomod package manager".format(
                invalid_files_print.strip(), file_suffix
            )
        )

    if len(subpaths) > 1 and dep_replacements:
        raise UnsupportedFeature(
            "Dependency replacements are only supported for a single go module path."
        )

    env_vars = {
        "GOCACHE": {"value": "deps/gomod", "kind": "path"},
        "GOPATH": {"value": "deps/gomod", "kind": "path"},
        "GOMODCACHE": {"value": "deps/gomod/pkg/mod", "kind": "path"},
    }
    env_vars.update(config.cachito_default_environment_variables.get("gomod", {}))
    update_request_env_vars(request_id, env_vars)

    packages_json_data = PackagesData()

    for i, subpath in enumerate(subpaths):
        log.info(
            "Fetching the gomod dependencies for request %d in subpath %s", request_id, subpath
        )
        set_request_state(
            request_id,
            "in_progress",
            f'Fetching the gomod dependencies at the "{subpath}" directory',
        )
        request = get_request(request_id)
        gomod_source_path = Path(bundle_dir.app_subpath(subpath).source_dir)
        try:
            gomod = resolve_gomod(
                gomod_source_path, request, dep_replacements, bundle_dir.source_dir
            )
        except GoModError:
            log.exception("Failed to fetch gomod dependencies for request %d", request_id)
            raise

        module_info = gomod["module"]

        packages_json_data.add_package(module_info, subpath, gomod["module_deps"])

        # add package deps
        for package in gomod["packages"]:
            pkg_info = package["pkg"]
            package_subpath = _package_subpath(module_info["name"], pkg_info["name"], subpath)
            packages_json_data.add_package(pkg_info, package_subpath, package.get("pkg_deps", []))

    _fail_if_parent_replacement_not_included(packages_json_data)
    packages_json_data.write_to_file(bundle_dir.gomod_packages_data)


def _package_subpath(module_name: str, package_name: str, module_subpath: str) -> str:
    """Get path from repository root to a package inside a module."""
    subpath = path_to_subpackage(module_name, package_name)
    return os.path.normpath(os.path.join(module_subpath, subpath))
