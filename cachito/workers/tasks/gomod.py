# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config
from cachito.workers.pkg_managers.general import (
    update_request_with_deps,
    update_request_with_package,
)
from cachito.workers.pkg_managers.gomod import resolve_gomod, path_to_subpackage
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.utils import (
    PackagesData,
    runs_if_request_in_progress,
    get_request,
    set_request_state,
)
from cachito.workers.paths import RequestBundleDir

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


@app.task
@runs_if_request_in_progress
def fetch_gomod_source(request_id, dep_replacements=None, package_configs=None):
    """
    Resolve and fetch gomod dependencies for a given request.

    :param int request_id: the Cachito request ID this is for
    :param list dep_replacements: dependency replacements with the keys "name" and "version"; only
        supported with a single path
    :param list package_configs: the list of optional package configurations submitted by the user
    :raises CachitoError: if the dependencies could not be retrieved
    """
    config = get_worker_config()
    if package_configs is None:
        package_configs = []

    bundle_dir: RequestBundleDir = RequestBundleDir(request_id)
    subpaths = [os.path.normpath(c["path"]) for c in package_configs if c.get("path")]

    if not subpaths:
        # Default to the root of the application source
        subpaths = [os.curdir]

    invalid_gomod_files = _find_missing_gomod_files(bundle_dir, subpaths)
    if invalid_gomod_files:
        invalid_files_print = "; ".join(invalid_gomod_files)
        file_suffix = "s" if len(invalid_gomod_files) > 1 else ""

        # missing gomod files is supported if there is only one path referenced
        if config.cachito_gomod_ignore_missing_gomod_file and len(subpaths) == 1:
            log.warning("go.mod file missing for request at %s", invalid_files_print)
            return

        raise CachitoError(
            "The {} file{} must be present for the gomod package manager".format(
                invalid_files_print.strip(), file_suffix
            )
        )

    if len(subpaths) > 1 and dep_replacements:
        raise CachitoError(
            "Dependency replacements are only supported for a single go module path."
        )

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
        gomod_source_path = str(bundle_dir.app_subpath(subpath).source_dir)
        try:
            gomod = resolve_gomod(
                gomod_source_path, request, dep_replacements, bundle_dir.source_dir
            )
        except CachitoError:
            log.exception("Failed to fetch gomod dependencies for request %d", request_id)
            raise

        if i == 0:
            env_vars = {
                "GOCACHE": {"value": "deps/gomod", "kind": "path"},
                "GOPATH": {"value": "deps/gomod", "kind": "path"},
                "GOMODCACHE": {"value": "deps/gomod/pkg/mod", "kind": "path"},
            }
            env_vars.update(config.cachito_default_environment_variables.get("gomod", {}))
        else:
            env_vars = None

        module_info = gomod["module"]

        update_request_with_package(request_id, module_info, env_vars, package_subpath=subpath)
        update_request_with_deps(request_id, module_info, gomod["module_deps"])
        packages_json_data.add_package(module_info, subpath, gomod["module_deps"])

        # add package deps
        for package in gomod["packages"]:
            pkg_info = package["pkg"]
            package_subpath = _package_subpath(module_info["name"], pkg_info["name"], subpath)

            update_request_with_package(request_id, pkg_info, package_subpath=package_subpath)
            if package.get("pkg_deps"):
                update_request_with_deps(request_id, pkg_info, package["pkg_deps"])

            packages_json_data.add_package(pkg_info, package_subpath, package.get("pkg_deps", []))

    packages_json_data.write_to_file(bundle_dir.gomod_packages_data)


def _package_subpath(module_name: str, package_name: str, module_subpath: str) -> str:
    """Get path from repository root to a package inside a module."""
    subpath = path_to_subpackage(module_name, package_name)
    return os.path.normpath(os.path.join(module_subpath, subpath))
