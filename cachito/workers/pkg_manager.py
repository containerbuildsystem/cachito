# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import subprocess
import tarfile
import tempfile

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config


log = logging.getLogger(__name__)


def resolve_gomod_deps(archive_path):
    """
    Resolve and fetch gomod dependencies for given app source archive.

    :param str archive_path: the full path to the application source code
    :raises CachitoError: if fetching dependencies fails
    """
    with tempfile.TemporaryDirectory(prefix='cachito-') as temp_dir:
        source_dir = _extract_app_src(archive_path, temp_dir)
        env = {
            'GOPATH': temp_dir,
            'GO111MODULE': 'on',
            'GOCACHE': temp_dir,
            'GOPROXY': get_worker_config().athens_url,
            'PATH': os.environ.get('PATH', ''),
        }
        cmd = ('go', 'list', '-m', 'all')

        go_list = subprocess.run(
            cmd, capture_output=True, universal_newlines=True, encoding='utf-8', env=env,
            cwd=source_dir)
        if go_list.returncode != 0:
            log.error(
                'Fetching gomod dependencies with "%s" failed with: %s',
                ' '.join(cmd),
                go_list.stderr,
            )
            raise CachitoError('Fetching gomod dependencies failed')

        deps = []
        for line in go_list.stdout.splitlines():
            parts = line.split(' ')
            if len(parts) == 1:
                # This is the application itself, not a dependency
                continue
            if len(parts) > 2:
                log.warning('Unexpected go module output: %s', line)
                continue
            if len(parts) == 2:
                deps.append({'type': 'gomod', 'name': parts[0], 'version': parts[1]})

        return deps


def _extract_app_src(archive_path, parent_dir):
    """
    Helper method to extract application source archive to a directory.

    :param str archive_path: the absolute path to the application source code
    :param str parent_dir: the absolute path to the extract target directory
    :returns: the absolute path of the extracted application source code
    :rtype: str
    """
    with tarfile.open(archive_path, 'r:gz') as archive:
        archive.extractall(parent_dir)
    return os.path.join(parent_dir, 'app')
