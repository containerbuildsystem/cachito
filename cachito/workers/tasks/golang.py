# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os

from cachito.errors import CachitoError
from cachito.workers.pkg_managers import resolve_gomod, update_request_with_deps
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.general import set_request_state
from cachito.workers.utils import get_request_bundle_dir


__all__ = ['fetch_gomod_source']
log = logging.getLogger(__name__)


@app.task
def fetch_gomod_source(request_id, auto_detect=False, dep_replacements=None):
    """
    Resolve and fetch gomod dependencies for a given request.

    :param int request_id: the Cachito request ID this is for
    :param bool auto_detect: automatically detect if the archive uses Go modules
    :param list dep_replacements: dependency replacements with the keys "name" and "version"
    """
    app_source_path = os.path.join(get_request_bundle_dir(request_id), 'app')
    if auto_detect:
        log.debug('Checking if the application source uses Go modules')
        go_mod_file = os.path.join(app_source_path, 'go.mod')
        if not os.path.exists(go_mod_file):
            log.info('The application source does not use Go modules')
            return

    log.info('Fetching gomod dependencies for request %d', request_id)
    request = set_request_state(request_id, 'in_progress', 'Fetching the golang dependencies')
    try:
        module, deps = resolve_gomod(app_source_path, request, dep_replacements)
    except CachitoError:
        log.exception('Failed to fetch gomod dependencies for request %d', request_id)
        raise

    env_vars = {}
    if len(deps):
        env_vars['GOPATH'] = env_vars['GOCACHE'] = 'deps/gomod'
    update_request_with_deps(request_id, deps, env_vars, 'gomod', [module])
