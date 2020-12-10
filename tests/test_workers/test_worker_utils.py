# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
from unittest import mock

import pytest

from cachito.errors import ValidationError
from cachito.workers import utils


@pytest.mark.parametrize(
    "child_path", ["some_subpath", ".", "/some_path/some_subpath", "../some_path/some_subpath"]
)
def test_ensure_local(child_path):
    utils.ensure_local(child_path, "/some_path")


def test_ensure_local_valid_symlink(tmp_path):
    os.symlink("some_subpath", tmp_path / "some_symlink")
    utils.ensure_local("some_symlink", tmp_path)


@pytest.mark.parametrize("child_path", ["/usr", "..", "foo/../bar/../.."])
@pytest.mark.parametrize("type_parent", [Path, str])
@pytest.mark.parametrize("type_child", [Path, str])
def test_ensure_local_not_subpath(child_path, type_parent, type_child):
    parent = type_parent("/some_path")
    child = type_child(child_path)

    expect_error = f"The destination of '{child_path}' is outside of '/some_path'"

    with pytest.raises(ValidationError, match=expect_error):
        utils.ensure_local(child, parent)


@pytest.mark.parametrize("symlink_src", ["/usr", "..", "foo/../bar/../.."])
def test_ensure_local_invalid_symlink(symlink_src, tmp_path):
    os.symlink(symlink_src, tmp_path / "some_symlink")

    expect_error = f"The destination of 'some_symlink' is outside of '{tmp_path}'"

    with pytest.raises(ValidationError, match=expect_error):
        utils.ensure_local("some_symlink", tmp_path)


@mock.patch("cachito.workers.utils.ensure_local")
def test_ensure_all_local(mock_ensure_local):
    utils.ensure_all_local(["foo", "bar"], "/some_path")
    mock_ensure_local.assert_has_calls(
        [mock.call("foo", "/some_path"), mock.call("bar", "/some_path")]
    )
