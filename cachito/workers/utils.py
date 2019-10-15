# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tarfile

from cachito.workers.config import get_worker_config


def extract_app_src(archive_path, parent_dir):
    """
    Extract an application source archive to a directory.

    :param str archive_path: the absolute path to the application source code
    :param str parent_dir: the absolute path to the extract target directory
    :returns: the absolute path of the extracted application source code
    :rtype: str
    """
    with tarfile.open(archive_path, 'r:*') as archive:
        archive.extractall(parent_dir)
    return os.path.join(parent_dir, 'app')


def get_request_bundle_dir(request_id):
    """
    Get the path to the directory where the bundle is being created for the request.

    :param int request_id: the request ID to get the directory for
    :return: the path to the where the bundle is being created
    :rtype: str
    """
    config = get_worker_config()
    return os.path.join(config.cachito_bundles_dir, 'temp', str(request_id))


def get_request_bundle_path(request_id):
    """
    Get the path to the request's bundle.

    :param int request_id: the request ID to get the bundle path for
    :return: the path to the request's bundle
    :rtype: str
    """
    config = get_worker_config()
    return os.path.join(config.cachito_bundles_dir, f'{request_id}.tar.gz')
