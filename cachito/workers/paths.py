# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import pathlib
import tempfile
from typing import Any

from cachito.common import paths
from cachito.workers.config import get_worker_config

log = logging.getLogger(__name__)


class RequestBundleDir(paths.RequestBundleDir):
    """
    Represents a concrete request bundle directory used on the worker.

    The root directory is set to the ``cachito_bundles_dir`` configuration.

    By default, this request bundle directory and its dependency directory will
    be created when this object is instantiated.

    :param int request_id: the request ID.
    """

    def __new__(cls, request_id):
        """Create a new Path object."""
        root_dir = get_worker_config().cachito_bundles_dir
        self = super().__new__(cls, request_id, root_dir)

        log.debug("Ensure directory %s exists.", self)
        log.debug("Ensure directory %s exists.", self.deps_dir)
        self.deps_dir.mkdir(parents=True, exist_ok=True)

        return self


# Similar with cachito.common.paths.RequestBundleDir, this base type will be the
# correct type for Linux or Windows individually.
base_path: Any = type(pathlib.Path())


class SourcesDir(base_path):
    """
    Represents a temporary sources directory tree for a package.

    The directory will be created automatically when this object is instantiated.

    :param str ref: the revision reference used to construct archive filename.
    """

    def __new__(cls, repo_name, ref):
        """Create a new Path object."""
        tempdir = tempfile.TemporaryDirectory(prefix="cachito-")
        self = super().__new__(cls, tempdir.name)

        self._temp_dir = tempdir
        self.archive_dir = self.joinpath("archive")
        self.archive_path = self.archive_dir.joinpath(f"{ref}.tar.gz")

        log.debug(f"Ensure directory {self.archive_dir} exists.")
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        return self
