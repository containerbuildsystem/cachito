# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tarfile


def extract_app_src(archive_path, parent_dir):
    """
    Extract an application source archive to a directory.

    :param str archive_path: the absolute path to the application source code
    :param str parent_dir: the absolute path to the extract target directory
    :returns: the absolute path of the extracted application source code
    :rtype: str
    """
    with tarfile.open(archive_path, "r:*") as archive:
        archive.extractall(parent_dir)
    return os.path.join(parent_dir, "app")
