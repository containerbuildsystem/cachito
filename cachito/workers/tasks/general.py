# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import shutil
import tarfile
from pathlib import Path
from typing import Any, Callable, List, Optional

import requests

from cachito.common.checksum import hash_file
from cachito.common.packages_data import PackagesData
from cachito.errors import CachitoError, ValidationError
from cachito.workers.scm import Git
from cachito.workers.paths import RequestBundleDir
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.utils import (
    runs_if_request_in_progress,
    get_request,
    set_request_state,
    set_packages_and_deps_counts,
)

__all__ = [
    "aggregate_packages_data",
    "create_bundle_archive",
    "failed_request_callback",
    "fetch_app_source",
    "finalize_request",
    "get_request",
    "save_bundle_archive_checksum",
]
log = logging.getLogger(__name__)


@app.task(priority=0)
@runs_if_request_in_progress
def fetch_app_source(url, ref, request_id, gitsubmodule=False):
    """
    Fetch the application source code that was requested and put it in long-term storage.

    :param str url: the source control URL to pull the source from
    :param str ref: the source control reference
    :param int request_id: the Cachito request ID this is for
    :param bool gitsubmodule: a bool to determine whether git submodules need to be processed.
    """
    log.info('Fetching the source from "%s" at reference "%s"', url, ref)
    set_request_state(request_id, "in_progress", "Fetching the application source")
    try:
        # Default to Git for now
        scm = Git(url, ref)
        scm.fetch_source(gitsubmodule=gitsubmodule)
    except requests.Timeout:
        raise CachitoError("The connection timed out while downloading the source")
    except CachitoError:
        log.exception('Failed to fetch the source from the URL "%s" and reference "%s"', url, ref)
        raise

    # Extract the archive contents to the temporary directory of where the bundle is being created.
    # This will eventually end up in the bundle the user downloads. This is extracted now since
    # some package managers may add dependency replacements, which require edits to source files.
    bundle_dir = RequestBundleDir(request_id)
    log.debug("Extracting %s to %s", scm.sources_dir.archive_path, bundle_dir)
    shutil.unpack_archive(str(scm.sources_dir.archive_path), str(bundle_dir))
    _enforce_sandbox(bundle_dir.source_root_dir)


def _enforce_sandbox(repo_root):
    """
    Check that there are no symlinks that try to leave the cloned repository.

    :param (str | Path) repo_root: absolute path to root of cloned repository
    :raises ValidationError: if any symlink points outside of cloned repository
    """
    for dirpath, subdirs, files in os.walk(repo_root):
        dirpath = Path(dirpath)

        for entry in subdirs + files:
            full_path = dirpath / entry
            real_path = full_path.resolve()
            try:
                real_path.relative_to(repo_root)
            except ValueError:
                # Unlike the real path, the full path is always relative to the root
                relative_path = str(full_path.relative_to(repo_root))
                raise ValidationError(
                    f"The destination of {relative_path!r} is outside of cloned repository"
                )


@app.task
def failed_request_callback(context, exc, traceback, request_id):
    """
    Wrap set_request_state for task error callbacks.

    :param celery.app.task.Context context: the context of the task failure
    :param Exception exc: the exception that caused the task failure
    :param int request_id: the ID of the Cachito request
    """
    if isinstance(exc, CachitoError):
        msg = str(exc)
    else:
        msg = "An unknown error occurred"

    set_request_state(request_id, "failed", msg)


def create_bundle_archive(request_id: int, flags: List[str]) -> None:
    """
    Create the bundle archive to be downloaded by the user.

    :param int request_id: the request the bundle is for
    :param list[str] flags: the list of request flags.
    """
    set_request_state(request_id, "in_progress", "Assembling the bundle archive")
    bundle_dir = RequestBundleDir(request_id)

    log.debug("Using %s for creating the bundle for request %d", bundle_dir, request_id)

    log.info("Creating %s", bundle_dir.bundle_archive_file)

    def filter_git_dir(tar_info):
        return tar_info if os.path.basename(tar_info.name) != ".git" else None

    tar_filter: Optional[Callable[[Any], Any]] = filter_git_dir
    if "include-git-dir" in flags:
        tar_filter = None

    with tarfile.open(bundle_dir.bundle_archive_file, mode="w:gz") as bundle_archive:
        # Add the source to the bundle. This is done one file/directory at a time in the parent
        # directory in order to exclude the app/.git folder.
        for item in bundle_dir.source_dir.iterdir():
            arc_name = os.path.join("app", item.name)
            bundle_archive.add(str(item), arc_name, filter=tar_filter)
        # Add the dependencies to the bundle
        bundle_archive.add(str(bundle_dir.deps_dir), "deps")


def aggregate_packages_data(request_id: int, pkg_managers: List[str]) -> PackagesData:
    """Aggregate packages data generated for each package manager into one unified data file.

    :param int request_id: the request id.
    """
    set_request_state(request_id, "in_progress", "Aggregating packages data")
    bundle_dir = RequestBundleDir(request_id)

    aggregated_data = PackagesData()
    for pkg_manager in pkg_managers:
        # Ensure git-submodule -> git_submodule
        data_file = getattr(bundle_dir, f"{pkg_manager.replace('-', '_')}_packages_data")
        aggregated_data.load(data_file)

    log.debug("Write request %s packages data into %s", request_id, bundle_dir.packages_data)
    aggregated_data.write_to_file(str(bundle_dir.packages_data))

    return aggregated_data


def save_bundle_archive_checksum(request_id: int) -> None:
    """Compute and store bundle archive's checksum.

    :param int request_id: the request id.
    """
    bundle_dir = RequestBundleDir(request_id)
    archive_file = bundle_dir.bundle_archive_file
    if not archive_file.exists():
        raise CachitoError(f"Bundle archive {archive_file} does not exist.")
    checksum = hash_file(archive_file).hexdigest()
    bundle_dir.bundle_archive_checksum.write_text(checksum, encoding="utf-8")


@app.task(priority=10)
@runs_if_request_in_progress
def finalize_request(request_id):
    """Execute tasks to finalize the request creation."""
    request = get_request(request_id)
    create_bundle_archive(request_id, request.get("flags", []))
    save_bundle_archive_checksum(request_id)
    data = aggregate_packages_data(request_id, request["pkg_managers"])
    set_packages_and_deps_counts(request_id, len(data.packages), len(data.all_dependencies))
    set_request_state(request_id, "complete", "Completed successfully")
