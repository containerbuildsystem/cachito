# SPDX-License-Identifier: GPL-3.0-or-later
import logging

import requests

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config
from cachito.workers.scm import Git
from cachito.workers.tasks.celery import app


__all__ = [
    'failed_request_callback',
    'fetch_app_source',
    'set_request_state',
]
log = logging.getLogger(__name__)


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
