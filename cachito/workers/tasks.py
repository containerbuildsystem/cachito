# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import tarfile

from celery import Celery
from celery.signals import celeryd_init
import requests

from cachito.workers.config import configure_celery, validate_celery_config, get_worker_config
from cachito.workers.pkg_manager import resolve_gomod_deps
from cachito.workers.scm import Git
from cachito.errors import CachitoError


log = logging.getLogger(__name__)
logging.basicConfig()
app = Celery()
configure_celery(app)
celeryd_init.connect(validate_celery_config)


@app.task
def fetch_app_source(url, ref, request_id_to_update=None):
    """
    Fetch the application source code that was requested and put it in long-term storage.

    :param str url: the source control URL to pull the source from
    :param str ref: the source control reference
    :param int request_id_to_update: the Cachito request ID this is for; if specified, this will
        update the request's state
    """
    log.info('Fetching the source from "%s" at reference "%s"', url, ref)
    if request_id_to_update:
        set_request_state(request_id_to_update, 'in_progress', 'Fetching the application source')
    try:
        # Default to Git for now
        scm = Git(url, ref)
        scm.fetch_source()
    except requests.Timeout:
        raise CachitoError('The connection timed out while downloading the source')
    except CachitoError:
        log.exception('Failed to fetch the source from the URL "%s" and reference "%s"', url, ref)
        raise

    return scm.archive_path


@app.task
def fetch_gomod_source(app_archive_path, copy_cache_to=None, request_id_to_update=None):
    """
    Resolve and fetch gomod dependencies for given app source archive.

    :param str app_archive_path: the full path to the application source code
    :param str copy_cache_to: path to copy artifacts from gomod cache
    :param int request_id_to_update: the Cachito request ID this is for; if specified, this will
        update the request's state
    """
    log.info('Fetching gomod dependencies for "%s"', app_archive_path)
    if request_id_to_update:
        set_request_state(request_id_to_update, 'in_progress', 'Fetching the golang dependencies')
    try:
        resolve_gomod_deps(app_archive_path, copy_cache_to)
    except CachitoError:
        log.exception('Failed to fetch gomod dependencies for "%s"', app_archive_path)
        raise
    # TODO: Store list of dependencies in DB via the API.
    return app_archive_path


@app.task
def assemble_source_code_archive(app_archive_path, deps_path, bundle_archive_path):
    """
    Creates an archive with the source code for application and its dependencies.

    :param str app_archive_path: the path to the archive of the application source code
    :param str deps_path: the path to the directory containing the dependencies source code
    :param str bundle_archive_path: the destination path of the assembled archive
    """
    log.info('Assembling source code archive in "%s"', bundle_archive_path)
    cachito_shared_dir = get_worker_config().cachito_shared_dir
    absolute_app_archive_path = os.path.join(cachito_shared_dir, app_archive_path)
    absolute_deps_path = os.path.join(cachito_shared_dir, deps_path)
    bundle_archive_path = os.path.join(cachito_shared_dir, bundle_archive_path)

    # Generate a tarball containing the application and dependencies source code
    with tarfile.open(bundle_archive_path, mode='w:gz') as bundle_archive:
        with tarfile.open(absolute_app_archive_path, mode='r:*') as app_archive:
            for member in app_archive.getmembers():
                bundle_archive.addfile(member, app_archive.extractfile(member.name))

        bundle_archive.add(absolute_deps_path, 'deps')


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
        rv = requests_auth_session.patch(request_url, json=payload, timeout=30)
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
