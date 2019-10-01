# SPDX-License-Identifier: GPL-3.0-or-later
import logging

from cachito.errors import CachitoError
from cachito.workers.pkg_manager import (
    archive_contains_path, resolve_gomod_deps, update_request_with_deps,
)
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.general import set_request_state


__all__ = ['fetch_gomod_source']
log = logging.getLogger(__name__)


@app.task
def fetch_gomod_source(app_archive_path, request_id_to_update=None, auto_detect=False):
    """
    Resolve and fetch gomod dependencies for given app source archive.

    :param str app_archive_path: the full path to the application source code
    :param int request_id_to_update: the Cachito request ID this is for; if specified, this will
        update the request's state
    :param bool auto_detect: automatically detect if the archive uses Go modules
    :return: the full path to the application source code
    :rtype: str
    """
    if auto_detect:
        log.debug('Checking if the application source uses Go modules')
        if not archive_contains_path(app_archive_path, 'app/go.mod'):
            log.info('The application source does not use Go modules')
            return app_archive_path

    log.info('Fetching gomod dependencies for "%s"', app_archive_path)
    if request_id_to_update:
        set_request_state(request_id_to_update, 'in_progress', 'Fetching the golang dependencies')

    try:
        deps = resolve_gomod_deps(app_archive_path, request_id_to_update)
    except CachitoError:
        log.exception('Failed to fetch gomod dependencies for "%s"', app_archive_path)
        raise

    if request_id_to_update:
        env_vars = {}
        if len(deps):
            env_vars['GOPATH'] = env_vars['GOCACHE'] = 'deps/gomod'
        update_request_with_deps(request_id_to_update, deps, env_vars, 'gomod')

    return app_archive_path
