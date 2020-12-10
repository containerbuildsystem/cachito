# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

from cachito.errors import ValidationError


def ensure_local(subpath, parent_path):
    """
    Check that the destination of subpath is inside parent_path, raise an error otherwise.

    Will resolve symlinks.

    This does not check if either path actually exists.

    :param (str | Path) subpath: relative or absolute path to a file or directory
    :param (str | Path) parent_path: absolute path to a directory
    :raise ValidationError: if subpath is not a subpath of parent_path
    """
    parent = Path(parent_path).resolve()
    child = (parent / subpath).resolve()
    try:
        child.relative_to(parent)
    except ValueError:
        raise ValidationError(
            f"The destination of {str(subpath)!r} is outside of {str(parent_path)!r}"
        )


def ensure_all_local(subpaths, parent_path):
    """
    Check that the destinations of all subpaths are inside parent_path, raise an error otherwise.

    Will resolve symlinks in all paths.

    This does not check if any path actually exists.

    :param list subpaths: list of relative or absoloute paths (str or Path objects)
    :param (str | Path) parent_path: absolute path to a directory
    :raise ValidationError: if any subpath is not a subpath of parent_path
    """
    for subpath in subpaths:
        ensure_local(subpath, parent_path)
