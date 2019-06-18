# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile

import requests

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config


log = logging.getLogger(__name__)


def resolve_gomod_deps(archive_path, copy_cache_to=None):
    """
    Resolve and fetch gomod dependencies for given app source archive.

    :param str archive_path: the full path to the application source code
    :param str copy_cache_to: path to copy artifacts from gomod cache
    :return: a list of dictionaries representing the gomod dependencies
    :rtype: list
    :raises CachitoError: if fetching dependencies fails
    """
    worker_config = get_worker_config()
    with tempfile.TemporaryDirectory(prefix='cachito-') as temp_dir:
        source_dir = _extract_app_src(archive_path, temp_dir)

        env = {
            'GOPATH': temp_dir,
            'GO111MODULE': 'on',
            'GOCACHE': temp_dir,
            'GOPROXY': worker_config.athens_url,
            'PATH': os.environ.get('PATH', ''),
        }

        run_params = {'env': env, 'cwd': source_dir}

        _run_cmd(('go', 'mod', 'download'), run_params)
        go_list_output = _run_cmd(('go', 'list', '-m', 'all'), run_params)

        deps = []
        for line in go_list_output.splitlines():
            parts = line.split(' ')
            if len(parts) == 1:
                # This is the application itself, not a dependency
                continue
            if len(parts) > 2:
                log.warning('Unexpected go module output: %s', line)
                continue
            if len(parts) == 2:
                deps.append({'type': 'gomod', 'name': parts[0], 'version': parts[1]})

        if copy_cache_to:
            # Copy gomod cache to requested location
            cache_path = os.path.join('pkg', 'mod', 'cache', 'download')
            src_cache_path = os.path.join(temp_dir, cache_path)
            dest_cache_path = os.path.join(
                worker_config.cachito_shared_dir, copy_cache_to, 'gomod', cache_path)
            shutil.copytree(src_cache_path, dest_cache_path)

        return deps


def update_request_with_deps(request_id, deps):
    """
    Update the Cachito request with the resolved dependencies.

    :param int request_id: the ID of the Cachito request
    :param list deps: the list of dependency dictionaries to record
    :raise CachitoError: if the request to the Cachito API fails
    """
    # Import this here to avoid a circular import
    from cachito.workers.requests import requests_auth_session
    config = get_worker_config()
    request_url = f'{config.cachito_api_url.rstrip("/")}/requests/{request_id}'

    log.info('Adding %d dependencies to request %d', len(deps), request_id)
    payload = {'dependencies': deps}
    try:
        rv = requests_auth_session.patch(request_url, json=payload, timeout=30)
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


def _extract_app_src(archive_path, parent_dir):
    """
    Helper method to extract application source archive to a directory.

    :param str archive_path: the absolute path to the application source code
    :param str parent_dir: the absolute path to the extract target directory
    :returns: the absolute path of the extracted application source code
    :rtype: str
    """
    with tarfile.open(archive_path, 'r:*') as archive:
        archive.extractall(parent_dir)
    return os.path.join(parent_dir, 'app')


def _run_cmd(cmd, params):
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
