# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os
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

    def __new__(cls, request_id, root, app_subpath=os.curdir):
        """
        Create a new Path object.

        :param int request_id: the ID of the request this bundle is for.
        :param str root: the root directory to the bundles.
        :param str app_subpath: an optional relative path to where the application resides in the
            source directory. This sets ``self.source_dir`` and all other related paths to
            start from that directory. If this is not set, it is assumed the application lives in
            the root of the source directory.
        """
        self = super().__new__(cls, root, "temp", str(request_id))
        self._request_id = request_id
        self._path_root = root

        self.source_root_dir = self.joinpath("app")
        self.source_dir = self.source_root_dir.joinpath(app_subpath)
        self.go_mod_file = self.source_dir.joinpath("go.mod")

        self.deps_dir = self.joinpath("deps")
        self.gomod_download_dir = self.joinpath("deps", "gomod", cls.go_mod_cache_download_part)

        self.node_modules = self.source_dir.joinpath("node_modules")
        self.npm_deps_dir = self.joinpath("deps", "npm")
        self.npm_package_file = self.source_dir.joinpath("package.json")
        self.npm_package_lock_file = self.source_dir.joinpath("package-lock.json")
        self.npm_shrinkwrap_file = self.source_dir.joinpath("npm-shrinkwrap.json")

        self.pip_deps_dir = self.joinpath("deps", "pip")

        self.yarn_deps_dir = self.joinpath("deps", "yarn")

        self.bundle_archive_file = Path(root, f"{request_id}.tar.gz")

        return self

    def app_subpath(self, subpath):
        """Create a new ``RequestBundleDir`` object with the sources pointed to the subpath."""
        return RequestBundleDir(self._request_id, self._path_root, subpath)

    def relpath(self, path):
        """Get the relative path of a path from the root of the source directory."""
        return os.path.relpath(path, start=self.source_root_dir)

    def rmtree(self):
        """Remove this directory tree entirely."""
        shutil.rmtree(str(self))
