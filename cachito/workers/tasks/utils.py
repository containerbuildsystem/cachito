# SPDX-License-Identifier: GPL-3.0-or-later
import base64
from pathlib import Path
from typing import Union, Callable

from cachito.errors import ValidationError

__all__ = ["make_base64_config_file", "AssertPackageFiles"]


def make_base64_config_file(content: str, dest_relpath: Union[str, Path]) -> dict:
    """
    Make a dict to be added as a base64-encoded config file to a request.

    :param str content: content of config file
    :param (str | Path) dest_relpath: relative path to config file from root of bundle directory
    :return: dict with "content", "path" and "type" keys
    """
    return {
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "path": str(dest_relpath),
        "type": "base64",
    }


class AssertPackageFiles:
    """Verify the presence (or absence) of files before processing a package manager."""

    def __init__(self, pkg_manager: str, repo_root: Union[str, Path], package_path: str = "."):
        """
        Initialize an instance.

        :param str pkg_manager: the package manager this instance is for, used in error messages
        :param (str | Path) repo_root: the absolute path to the root of a cloned repository
        :param str package_path: optional relative path from the repo root to a package directory
        """
        self._pkg_manager = pkg_manager
        self._root_dir = Path(repo_root)
        self._pkg_dir = self._root_dir / package_path

    def present(self, path: str):
        """
        Check that file path exists and is a file.

        :param str path: relative path to file/dir in package
        :raise ValidationError: if file path does not exist or is not a file
        """
        self._assert(Path.exists, path, True, "the {relpath} file must be present")
        self._assert(Path.is_file, path, True, "{relpath} must be a file")

    def dir_present(self, path: str):
        """
        Check that file path exists and is a directory.

        :param str path: relative path to file/dir in package
        :raise ValidationError: if file path does not exist or is not a directory
        """
        self._assert(Path.exists, path, True, "the {relpath} directory must be present")
        self._assert(Path.is_dir, path, True, "{relpath} must be a directory")

    def absent(self, path: str):
        """
        Check that file path does not exist or is not a file.

        :param str path: relative path to file/dir in package
        :raise ValidationError: if file path exists and is a file
        """
        self._assert(Path.is_file, path, False, "the {relpath} file must not be present")

    def dir_absent(self, path: str):
        """
        Check that file path does not exist or is not a directory.

        :param str path: relative path to file/dir in package
        :raise ValidationError: if file path exists and is a directory
        """
        self._assert(Path.is_dir, path, False, "the {relpath} directory must not be present")

    def _assert(
        self, check_presence: Callable[[Path], bool], path: str, expect: bool, err_template: str
    ):
        """
        Make an assertion about the presence of a file, raise an error if it fails.

        Turns `path` into an absolute path, calls check_presence() on it and compares the result
        with the expected value.

        :param (Path) -> bool check_presence: method to check file presence, e.g. Path.is_file
        :param str path: relative path to file/directory from root of package directory
        :param bool expect: expect the file/directory to be present?
        :param str err_template: error message which may contain {relpath} as a placeholder
        :raises ValidationError: if the assertion fails
        """
        fullpath = self._pkg_dir / path

        if check_presence(fullpath) != expect:
            relpath = fullpath.relative_to(self._root_dir)
            err_msg = err_template.format(relpath=relpath)
            raise ValidationError(f"File check failed for {self._pkg_manager}: {err_msg}")
