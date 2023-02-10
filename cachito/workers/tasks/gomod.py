# SPDX-License-Identifier: GPL-3.0-or-later
import itertools
import logging
import os
from pathlib import Path

from cachito.common.packages_data import PackagesData
from cachito.errors import InvalidRepoStructure, InvalidRequestData
from cachito.workers import run_cmd
from cachito.workers.cachi2_compatibility import Cachi2Adapter, set_cachi2_config
from cachito.workers.config import get_worker_config
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers import gomod
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.utils import get_request, runs_if_request_in_progress, set_request_state

__all__ = ["fetch_gomod_source"]
log = logging.getLogger(__name__)


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
    go_modules = [package for package in packages_json_data.packages if package["type"] == "gomod"]
    go_packages = [
        package for package in packages_json_data.packages if package["type"] == "go-package"
    ]

    module_names = [module["name"] for module in go_modules]

    for package in itertools.chain(go_modules, go_packages):
        for dependency in package.get("dependencies", []):
            if dependency["version"] and ".." in Path(dependency["version"]).parts:
                pkg_module_name = gomod.match_parent_module(package["name"], module_names)
                if pkg_module_name is None:
                    # This should be impossible
                    raise RuntimeError(
                        f"Could not find parent Go module for package: {package['name']}"
                    )

                dep_normpath = os.path.normpath(
                    os.path.join(pkg_module_name, dependency["version"])
                )
                dep_module_name = gomod.match_parent_module(dep_normpath, module_names)
                if dep_module_name is None:
                    raise InvalidRequestData(
                        (
                            f"Could not find a Go module in this request containing {dep_normpath} "
                            f"while processing dependency {dependency} of package "
                            f"{package['name']}. Please tell Cachito to process the module which "
                            f"contains the dependency. Perhaps the parent module of "
                            f"{pkg_module_name}?"
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
    version_output = run_cmd(["go", "version"], {})
    log.info(f"Go version: {version_output.strip()}")

    config = get_worker_config()
    if not package_configs:
        package_configs = [{"path": "."}]

    bundle_dir: RequestBundleDir = RequestBundleDir(request_id)
    subpaths = [os.path.normpath(c["path"]) for c in package_configs]

    if not subpaths:
        # Default to the root of the application source
        subpaths = [os.curdir]

    # TODO: this is supposed to be in cachi2 of course
    _fail_if_bundle_dir_has_workspaces(bundle_dir, subpaths)

    if (
        len(subpaths) == 1
        and config.cachito_gomod_ignore_missing_gomod_file
        and not bundle_dir.app_subpath(subpaths[0]).go_mod_file.exists()
    ):
        log.warning("go.mod file missing at %s, skipping", subpaths[0])
        return

    set_request_state(request_id, "in_progress", "Fetching gomod dependencies")

    set_cachi2_config(get_worker_config())
    cachi2_adapter = Cachi2Adapter(
        request_json=get_request(request_id),
        request_bundle=bundle_dir,
        package_manager="gomod",
    )
    cachi2_output = cachi2_adapter.run_package_manager(package_configs, dep_replacements)
    cachi2_adapter.update_request_env_vars(cachi2_output)
    packages_data = cachi2_adapter.update_request_packages(cachi2_output)

    # TODO: local deps allowlist garbage
    # or "oops we accidentally dropped it"

    _fail_if_parent_replacement_not_included(packages_data)
