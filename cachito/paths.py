# SPDX-License-Identifier: GPL-3.0-or-later

import logging
from pathlib import Path
import shutil

log = logging.getLogger(__name__)


# Subclassing from type(Path()) is a workaround because pathlib does not
# support subclass from Path directly. This base type will be the correct type
# for Linux or Windows individually.
class RequestBundleDir(type(Path())):
    """
    Represents a directory tree for a request.

    :param int request_id: the request ID.
    :param str root: the root directory. A request bundle directory will be
        created under ``root/temp/``.
    """

    go_mod_cache_download_part = Path("pkg", "mod", "cache", "download")

    def __new__(cls, request_id, root):
        """Create a new Path object."""
        self = super().__new__(cls, root, "temp", str(request_id))

        self.source_dir = self.joinpath("app")
        self.go_mod_file = self.joinpath("app", "go.mod")

        self.deps_dir = self.joinpath("deps")
        self.gomod_download_dir = self.joinpath("deps", "gomod", cls.go_mod_cache_download_part)

        self.npm_deps_dir = self.joinpath("deps", "npm")
        self.npm_package_lock_file = self.joinpath("app", "package-lock.json")
        self.npm_shrinkwrap_file = self.joinpath("app", "npm-shrinkwrap.json")

        self.bundle_archive_file = Path(root, f"{request_id}.tar.gz")

        return self

    def rmtree(self):
        """Remove this directory tree entirely."""
        shutil.rmtree(str(self))
