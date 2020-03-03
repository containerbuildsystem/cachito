# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os

from cachito.errors import CachitoError
from cachito.workers.pkg_managers import resolve_bundler, update_request_with_deps
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.general import set_request_state
from cachito.workers.utils import get_request_bundle_dir


__all__ = ['fetch_bundler_source']
log = logging.getLogger(__name__)


@app.task
def fetch_bundler_source(request_id, auto_detect=False):
    """
    Resolve and fetch bundler dependencies for a given request.

    :param int request_id: the Cachito request ID this is for
    :param bool auto_detect: automatically detect if the application uses bundler for dependency management
    """
    app_source_path = os.path.join(get_request_bundle_dir(request_id), 'app')
    if auto_detect:
        log.debug('Checking if the application source uses bundler for dependency management')
        gemfile_lock = os.path.join(app_source_path, 'Gemfile.lock')
        if not os.path.exists(gemfile_lock):
            log.info('The application source does not use bundler')
            return

    log.info('Fetching bundler dependencies for request %d', request_id)
    request = set_request_state(request_id, 'in_progress', 'Fetching the ruby dependencies')
    try:
        ruby_app, deps = resolve_bundler(app_source_path, request)
    except CachitoError:
        log.exception('Failed to fetch ruby dependencies for request %d', request_id)
        raise

    env_vars = {}
    if len(deps):
        env_vars['BUNDLE_PATH'] = 'vendor/cache'
    update_request_with_deps(request_id, deps, env_vars, 'bundler', [ruby_app])
