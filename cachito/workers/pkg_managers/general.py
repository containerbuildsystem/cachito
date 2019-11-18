# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import shutil
import subprocess

import requests

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config
from cachito.workers.utils import get_request_bundle_dir

__all__ = ['add_deps_to_bundle', 'run_cmd', 'update_request_with_deps']

log = logging.getLogger(__name__)


def update_request_with_deps(request_id, deps, env_vars=None, pkg_manager=None, packages=None):
    """
    Update the Cachito request with the resolved dependencies.

    :param int request_id: the ID of the Cachito request
    :param list deps: the list of dependency dictionaries to record
    :param dict env_vars: mapping of environment variables to record
    :param str pkg_manager: a package manager to add to the request if auto-detection was used
    :param list packages: the list of packages that were resolved
    :raise CachitoError: if the request to the Cachito API fails
    """
    # Import this here to avoid a circular import
    from cachito.workers.requests import requests_auth_session
    config = get_worker_config()
    request_url = f'{config.cachito_api_url.rstrip("/")}/requests/{request_id}'

    log.info('Adding %d dependencies to request %d', len(deps), request_id)
    for index in range(0, len(deps), config.cachito_deps_patch_batch_size):
        batch_upper_limit = index + config.cachito_deps_patch_batch_size
        payload = {'dependencies': deps[index:batch_upper_limit]}
        if index == 0:
            if env_vars:
                log.info('Adding environment variables to the request %d: %s', request_id, env_vars)
                payload['environment_variables'] = env_vars
            if pkg_manager:
                log.info(
                    'Adding the package manager "%s" to the request %d',
                    pkg_manager, request_id,
                )
                payload['pkg_managers'] = [pkg_manager]
            if packages:
                log.info('Adding the packages "%s" to the request %d', packages, request_id)
                payload['packages'] = packages
        try:
            log.info('Patching deps {} through {} out of {}'.format(
                index + 1, min(batch_upper_limit, len(deps)), len(deps)))
            rv = requests_auth_session.patch(
                request_url, json=payload, timeout=config.cachito_api_timeout)
        except requests.RequestException:
            msg = f'The connection failed when setting the dependencies on request {request_id}'
            log.exception(msg)
            raise CachitoError(msg)

        if not rv.ok:
            log.error(
                'The worker failed to set the dependencies on request %d. The status was %d. '
                'The text was:\n%s',
                request_id, rv.status_code, rv.text,
            )
            raise CachitoError(f'Setting the dependencies on request {request_id} failed')


def add_deps_to_bundle(src_deps_path, dest_cache_path, request_id):
    """
    Add the dependencies to a directory that will be part of the bundle archive.

    :param str src_deps_path: the path to the dependencies to add to the bundle archive
    :param str dest_cache_path: the relative path in the "deps" directory in the bundle to add the
        content of src_deps_path to
    :param int request_id: the request the bundle is for
    """
    deps_path = os.path.join(get_request_bundle_dir(request_id), 'deps')
    if not os.path.exists(deps_path):
        log.debug('Creating %s', deps_path)
        os.makedirs(deps_path, exist_ok=True)

    dest_deps_path = os.path.join(deps_path, dest_cache_path)
    log.debug('Adding dependencies from %s to %s', src_deps_path, dest_deps_path)
    shutil.copytree(src_deps_path, dest_deps_path)


def run_cmd(cmd, params):
    """
    Run the given command with provided parameters.

    :param iter cmd: iterable representing command to be executed
    :param dict params: keyword parameters for command execution
    :returns: the command output
    :rtype: str
    """
    params.setdefault('capture_output', True)
    params.setdefault('universal_newlines', True)
    params.setdefault('encoding', 'utf-8')

    response = subprocess.run(cmd, **params)

    if response.returncode != 0:
        log.error(
            'Processing gomod dependencies with "%s" failed with: %s',
            ' '.join(cmd),
            response.stderr,
        )
        raise CachitoError('Processing gomod dependencies failed')

    return response.stdout
