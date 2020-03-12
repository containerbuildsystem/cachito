# SPDX-License-Identifier: GPL-3.0-or-later
# from datetime import datetime
import functools
import logging
import os
# import re
import tempfile

# import git
# import semver

# from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config
from cachito.workers.pkg_managers.general import add_deps_to_bundle, run_cmd

# __all__ = ['get_golang_version', 'resolve_bundler']
__all__ = ['resolve_bundler']

log = logging.getLogger(__name__)
run_bundler_cmd = functools.partial(run_cmd, exc_msg='Processing bundler dependencies failed')


def resolve_bundler(app_source_path, request):
    """
    Resolve and fetch bundler dependencies for given app source archive.

    :param str app_source_path: the full path to the application source code
    :param dict request: the Cachito request this is for
    :return: a tuple of the ruby gem itself and the list of dictionaries representing the
        dependencies
    :rtype: (dict, list)
    :raises CachitoError: if fetching dependencies fails
    """

    worker_config = get_worker_config()
    with tempfile.TemporaryDirectory(prefix='cachito-') as temp_dir:
        env = {
            'BUNDLE_PATH': temp_dir,
            'BUNDLE_DISABLE_SHARED_GEMS': '1',
            'PATH': os.environ.get('PATH', ''),
        }

        run_params = {'env': env, 'cwd': app_source_path}

        run_bundler_cmd(
            ('bundle', 'config', 'mirror.http://rubygems.org', worker_config.cachito_nexus_url),
            run_params
        )
        run_bundler_cmd(
            ('bundle', 'config', 'mirror.https://rubygems.org', worker_config.cachito_nexus_url),
            run_params
        )

        log.info('Downloading the bundler dependencies')
        path_param = '--path=' + temp_dir
        bundle_package_output = run_bundler_cmd(('bundle', 'package', '--no-install', '--all', path_param), run_params)

        deps = []
        for line in bundle_package_output.splitlines():
            log.debug('read line: %s', line)
            if line.strip().startswith('Fetching'):
                parts = [part for part in line.split(' ') if part != '']
                if len(parts) == 3:
                    deps.append({
                        'name': parts[1],
                        'type': 'bundler',
                        'version': parts[2],
                    })
                    log.debug('Added dependency: %s @ version: %s', parts[1], parts[2])
                else:
                    log.warning('Unexpected bundler list output: %s', line)

        app = {
            'name': request['repo'],
            'type': 'bundler',
            'version': request['ref'],
        }

        # Add the bundler cache to the bundle the user will later download
        cache_path = os.path.join('vendor', 'cache')
        src_cache_path = os.path.join(temp_dir, cache_path)
        dest_cache_path = os.path.join('bundler', cache_path)
        add_deps_to_bundle(src_cache_path, dest_cache_path, request['id'])

        return app, deps
