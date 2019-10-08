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
def fetch_gomod_source(app_archive_path, request_id, auto_detect=False):
    """
    Resolve and fetch gomod dependencies for given app source archive.

    :param str app_archive_path: the full path to the application source code
    :param int request_id: the Cachito request ID this is for
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
    set_request_state(request_id, 'in_progress', 'Fetching the golang dependencies')

    try:
        deps = resolve_gomod_deps(app_archive_path, request_id)
    except CachitoError:
        log.exception('Failed to fetch gomod dependencies for "%s"', app_archive_path)
        raise

    env_vars = {}
    if len(deps):
        env_vars['GOPATH'] = env_vars['GOCACHE'] = 'deps/gomod'
    update_request_with_deps(request_id, deps, env_vars, 'gomod')

    return app_archive_path
