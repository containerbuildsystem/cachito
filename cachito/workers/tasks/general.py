# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import shutil
import tarfile
from pathlib import Path

import requests

from cachito.errors import CachitoError, ValidationError
from cachito.workers.config import get_worker_config
from cachito.workers.scm import Git
from cachito.workers.paths import RequestBundleDir
from cachito.workers.tasks.celery import app

__all__ = [
    "create_bundle_archive",
    "failed_request_callback",
    "fetch_app_source",
    "set_request_state",
]
log = logging.getLogger(__name__)


@app.task(priority=0)
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
def set_request_state(request_id, state, state_reason):
    """
    Set the state of the request using the Cachito API.

    :param int request_id: the ID of the Cachito request
    :param str state: the state to set the Cachito request to
    :param str state_reason: the state reason to set the Cachito request to
    :return: the updated request
    :rtype: dict
    :raise CachitoError: if the request to the Cachito API fails
    """
    # Import this here to avoid a circular import
    from cachito.workers.requests import requests_auth_session

    config = get_worker_config()
    request_url = f'{config.cachito_api_url.rstrip("/")}/requests/{request_id}'

    log.info(
        'Setting the state of request %d to "%s" with the reason "%s"',
        request_id,
        state,
        state_reason,
    )
    payload = {"state": state, "state_reason": state_reason}
    try:
        rv = requests_auth_session.patch(
            request_url, json=payload, timeout=config.cachito_api_timeout
        )
    except requests.RequestException:
        msg = f'The connection failed when setting the state to "{state}" on request {request_id}'
        log.exception(msg)
        raise CachitoError(msg)

    if not rv.ok:
        log.error(
            'The worker failed to set request %d to the "%s" state. The status was %d. '
            "The text was:\n%s",
            request_id,
            state,
            rv.status_code,
            rv.text,
        )
        raise CachitoError(f'Setting the state to "{state}" on request {request_id} failed')

    return rv.json()


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


@app.task(priority=10)
def create_bundle_archive(request_id):
    """
    Create the bundle archive to be downloaded by the user.

    :param int request_id: the request the bundle is for
    """
    request = set_request_state(request_id, "in_progress", "Assembling the bundle archive")

    bundle_dir = RequestBundleDir(request_id)

    log.debug("Using %s for creating the bundle for request %d", bundle_dir, request_id)

    log.info("Creating %s", bundle_dir.bundle_archive_file)

    def filter_git_dir(tar_info):
        return tar_info if os.path.basename(tar_info.name) != ".git" else None

    tar_filter = filter_git_dir
    if "include-git-dir" in request.get("flags", []):
        tar_filter = None

    with tarfile.open(bundle_dir.bundle_archive_file, mode="w:gz") as bundle_archive:
        # Add the source to the bundle. This is done one file/directory at a time in the parent
        # directory in order to exclude the app/.git folder.
        for item in bundle_dir.source_dir.iterdir():
            arc_name = os.path.join("app", item.name)
            bundle_archive.add(str(item), arc_name, filter=tar_filter)
        # Add the dependencies to the bundle
        bundle_archive.add(str(bundle_dir.deps_dir), "deps")

    set_request_state(request_id, "complete", "Completed successfully")
