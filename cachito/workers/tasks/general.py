# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import tarfile

import requests

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config
from cachito.workers.scm import Git
from cachito.workers.tasks.celery import app
from cachito.workers.utils import extract_app_src, get_request_bundle_dir, get_request_bundle_path


__all__ = [
    'create_bundle_archive',
    'failed_request_callback',
    'fetch_app_source',
    'set_request_state',
]
log = logging.getLogger(__name__)


@app.task
def fetch_app_source(url, ref, request_id):
    """
    Fetch the application source code that was requested and put it in long-term storage.

    :param str url: the source control URL to pull the source from
    :param str ref: the source control reference
    :param int request_id: the Cachito request ID this is for
    """
    log.info('Fetching the source from "%s" at reference "%s"', url, ref)
    set_request_state(request_id, 'in_progress', 'Fetching the application source')
    try:
        # Default to Git for now
        scm = Git(url, ref)
        scm.fetch_source()
    except requests.Timeout:
        raise CachitoError('The connection timed out while downloading the source')
    except CachitoError:
        log.exception('Failed to fetch the source from the URL "%s" and reference "%s"', url, ref)
        raise

    # Extract the archive contents to the temporary directory of where the bundle is being created.
    # This will eventually end up in the bundle the user downloads. This is extracted now since
    # some package managers may add dependency replacements, which require edits to source files.
    request_bundle_dir = get_request_bundle_dir(request_id)
    if not os.path.exists(request_bundle_dir):
        log.debug('Creating %s', request_bundle_dir)
        os.makedirs(request_bundle_dir, exist_ok=True)
    log.debug('Extracting %s to %s', scm.archive_path, request_bundle_dir)
    extract_app_src(scm.archive_path, request_bundle_dir)


@app.task
def set_request_state(request_id, state, state_reason):
    """
    Set the state of the request using the Cachito API.

    :param int request_id: the ID of the Cachito request
    :param str state: the state to set the Cachito request to
    :param str state_reason: the state reason to set the Cachito request to
    :raise CachitoError: if the request to the Cachito API fails
    """
    # Import this here to avoid a circular import
    from cachito.workers.requests import requests_auth_session

    config = get_worker_config()
    request_url = f'{config.cachito_api_url.rstrip("/")}/requests/{request_id}'

    log.info(
        'Setting the state of request %d to "%s" with the reason "%s"',
        request_id, state, state_reason
    )
    payload = {'state': state, 'state_reason': state_reason}
    try:
        rv = requests_auth_session.patch(
            request_url, json=payload, timeout=config.cachito_api_timeout)
    except requests.RequestException:
        msg = f'The connection failed when setting the state to "{state}" on request {request_id}'
        log.exception(msg)
        raise CachitoError(msg)

    if not rv.ok:
        log.error(
            'The worker failed to set request %d to the "%s" state. The status was %d. '
            'The text was:\n%s',
            request_id, state, rv.status_code, rv.text,
        )
        raise CachitoError(f'Setting the state to "{state}" on request {request_id} failed')


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
        msg = 'An unknown error occurred'

    set_request_state(request_id, 'failed', msg)


@app.task
def create_bundle_archive(request_id):
    """
    Create the bundle archive to be downloaded by the user.

    :param int request_id: the request the bundle is for
    """
    set_request_state(request_id, 'in_progress', 'Assembling the bundle archive')

    bundle_dir = get_request_bundle_dir(request_id)
    source_path = os.path.join(bundle_dir, 'app')
    deps_path = os.path.join(bundle_dir, 'deps')
    log.debug('Using %s for creating the bundle for request %d', bundle_dir, request_id)

    if not os.path.isdir(deps_path):
        log.debug('No deps are present at %s, creating an empty directory', deps_path)
        os.makedirs(deps_path, exist_ok=True)

    bundle_archive_path = get_request_bundle_path(request_id)
    log.info('Creating %s', bundle_archive_path)
    with tarfile.open(bundle_archive_path, mode='w:gz') as bundle_archive:
        # Add the source to the bundle
        bundle_archive.add(source_path, 'app')
        # Add the dependencies to the bundle
        bundle_archive.add(deps_path, 'deps')
