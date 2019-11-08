# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import tempfile

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config
from cachito.workers.pkg_managers.general import add_deps_to_bundle, run_cmd

__all__ = ['resolve_gomod_deps']

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
            run_cmd(('go', 'clean', '-modcache'), {'env': env})
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
            run_cmd(('go', 'mod', 'edit', '-replace', f'{name}={new_name}@{version}'), run_params)

        log.info('Downloading the gomod dependencies')
        run_cmd(('go', 'mod', 'download'), run_params)
        go_list_output = run_cmd(
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
