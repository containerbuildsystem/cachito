# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import shutil
import subprocess
import tempfile

import requests

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config
from cachito.workers.utils import get_request_bundle_dir


log = logging.getLogger(__name__)


class GoCacheTemporaryDirectory(tempfile.TemporaryDirectory):
    """
    A wrapper around the TemporaryDirectory context manager to also run `go clean -modcache`.

    The files in the Go cache are read-only by default and cause the default clean up behavior of
    tempfile.TemporaryDirectory to fail with a permission error. A way around this is to run
    `go clean -modcache` before the default clean up behavior is run.
    """
    def __exit__(self, exc, value, tb):
        """
        Clean up temporary directory by first cleaning up the Go cache.
        """
        try:
            env = {'GOPATH': self.name, 'GOCACHE': self.name}
            _run_cmd(('go', 'clean', '-modcache'), {'env': env})
        finally:
            super().__exit__(exc, value, tb)


def resolve_gomod_deps(app_source_path, request_id, dep_replacements=None):
    """
    Resolve and fetch gomod dependencies for given app source archive.

    :param str app_source_path: the full path to the application source code
    :param int request_id: the request ID of the bundle to add the gomod deps to
    :param list dep_replacements: dependency replacements with the keys "name" and "version"; this
        results in a series of `go mod edit -replace` commands
    :return: a list of dictionaries representing the gomod dependencies
    :rtype: list
    :raises CachitoError: if fetching dependencies fails
    """
    if not dep_replacements:
        dep_replacements = []

    worker_config = get_worker_config()
    with GoCacheTemporaryDirectory(prefix='cachito-') as temp_dir:
        env = {
            'GOPATH': temp_dir,
            'GO111MODULE': 'on',
            'GOCACHE': temp_dir,
            'GOPROXY': worker_config.cachito_athens_url,
            'PATH': os.environ.get('PATH', ''),
        }

        run_params = {'env': env, 'cwd': app_source_path}

        # Collect all the dependency names that are being replaced to later verify if they were
        # all used
        replaced_dep_names = set()
        for dep_replacement in dep_replacements:
            name = dep_replacement['name']
            replaced_dep_names.add(name)
            new_name = dep_replacement.get('new_name', name)
            version = dep_replacement['version']
            log.info('Applying the gomod replacement %s => %s@%s', name, new_name, version)
            _run_cmd(('go', 'mod', 'edit', '-replace', f'{name}={new_name}@{version}'), run_params)

        log.info('Downloading the gomod dependencies')
        _run_cmd(('go', 'mod', 'download'), run_params)
        go_list_output = _run_cmd(
            ('go', 'list', '-m', '-f', '{{.Path}} {{.Version}} {{.Replace}}', 'all'), run_params)

        deps = []
        # Keep track of which dependency replacements were actually applied to verify they were all
        # used later
        used_replaced_dep_names = set()
        for line in go_list_output.splitlines():
            # If there is no "replace" directive used on the dependency, then the last column will
            # be "<nil>"
            parts = [part for part in line.split(' ') if part not in ('', '<nil>')]
            if len(parts) == 1:
                # This is the application itself, not a dependency
                continue

            replaces = None
            if len(parts) == 3:
                # If a Go module uses a "replace" directive to a local path, it will be shown as:
                # k8s.io/metrics v0.0.0 ./staging/src/k8s.io/metrics
                # In this case, just take the left side.
                parts = parts[0:2]
            elif len(parts) == 4:
                # If a Go module uses a "replace" directive, then it will be in the format:
                # github.com/pkg/errors v0.8.0 github.com/pkg/errors v0.8.1
                # In this case, just take the right side since that is the actual
                # dependency being used
                old_name, old_version = parts[0], parts[1]
                # Only keep track of user provided replaces. There could be existing "replace"
                # directives in the go.mod file, but they are an implementation detail specific to
                # Go and they don't need to be recorded in Cachito.
                if old_name in replaced_dep_names:
                    used_replaced_dep_names.add(old_name)
                    replaces = {'type': 'gomod', 'name': old_name, 'version': old_version}
                parts = parts[2:]

            if len(parts) == 2:
                deps.append({
                    'name': parts[0],
                    'replaces': replaces,
                    'type': 'gomod',
                    'version': parts[1],
                })
            else:
                log.warning('Unexpected go module output: %s', line)

        unused_dep_replacements = replaced_dep_names - used_replaced_dep_names
        if unused_dep_replacements:
            raise CachitoError(
                'The following gomod dependency replacements don\'t apply: '
                f'{", ".join(unused_dep_replacements)}'
            )

        # Add the gomod cache to the bundle the user will later download
        cache_path = os.path.join('pkg', 'mod', 'cache', 'download')
        src_cache_path = os.path.join(temp_dir, cache_path)
        dest_cache_path = os.path.join('gomod', cache_path)
        add_deps_to_bundle(src_cache_path, dest_cache_path, request_id)

        return deps


def update_request_with_deps(request_id, deps, env_vars=None, pkg_manager=None):
    """
    Update the Cachito request with the resolved dependencies.

    :param int request_id: the ID of the Cachito request
    :param list deps: the list of dependency dictionaries to record
    :param dict env_vars: mapping of environment variables to record
    :param str pkg_manager: a package manager to add to the request if auto-detection was used
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
