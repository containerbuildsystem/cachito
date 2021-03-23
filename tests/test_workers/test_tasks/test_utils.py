# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
from unittest import mock

import pytest

from cachito.errors import ValidationError
from cachito.workers.tasks import utils
from cachito.workers.requests import requests_session
from cachito.workers.tasks.utils import get_request_state

from tests.helper_utils import write_file_tree


@pytest.fixture
def assert_files_testdir(tmp_path):
    tree = {
        "present_file": "",
        "present_dir": {},
        "sub": {
            "present_file": "",
            "present_dir": {},
            "can_black_stop_collapsing_dicts_that_I_want_to_keep_multiline_please": "",
        },
    }
    write_file_tree(tree, tmp_path)
    return tmp_path


@pytest.mark.parametrize("path", ["app/foo.cfg", Path("app/foo.cfg")])
def test_make_base64_config_file(path):
    content = "foo = bar"
    expected = {"content": "Zm9vID0gYmFy", "path": "app/foo.cfg", "type": "base64"}
    assert utils.make_base64_config_file(content, path) == expected


class TestAssertPackageFiles:
    """Tests for the AssertPackageFiles class."""

    def _do_assert(self, method_name, package_path, filepath, expect_error, assert_files_testdir):
        assert_files = utils.AssertPackageFiles("yarn", assert_files_testdir, package_path)
        assert_method = getattr(assert_files, method_name)

        if expect_error:
            expect_error_full = f"File check failed for yarn: {expect_error}"
            with pytest.raises(ValidationError, match=expect_error_full):
                assert_method(filepath)
        else:
            assert_method(filepath)

    @pytest.mark.parametrize(
        "package_path, filepath, expect_error",
        [
            (".", "present_file", None),
            ("sub", "present_file", None),
            (".", "absent_file", "the absent_file file must be present"),
            (".", "present_dir", "present_dir must be a file"),
            ("sub", "absent_file", "the sub/absent_file file must be present"),
            ("sub", "present_dir", "sub/present_dir must be a file"),
        ],
    )
    def test_file_present(self, package_path, filepath, expect_error, assert_files_testdir):
        """Test the present() method."""
        self._do_assert("present", package_path, filepath, expect_error, assert_files_testdir)

    @pytest.mark.parametrize(
        "package_path, filepath, expect_error",
        [
            (".", "present_dir", None),
            ("sub", "present_dir", None),
            (".", "absent_dir", "the absent_dir directory must be present"),
            (".", "present_file", "present_file must be a directory"),
            ("sub", "absent_dir", "the sub/absent_dir directory must be present"),
            ("sub", "present_file", "sub/present_file must be a directory"),
        ],
    )
    def test_dir_present(self, package_path, filepath, expect_error, assert_files_testdir):
        """Test the dir_present() method."""
        self._do_assert("dir_present", package_path, filepath, expect_error, assert_files_testdir)

    @pytest.mark.parametrize(
        "package_path, filepath, expect_error",
        [
            (".", "absent_file", None),
            (".", "present_dir", None),
            ("sub", "absent_file", None),
            ("sub", "present_dir", None),
            (".", "present_file", "the present_file file must not be present"),
            ("sub", "present_file", "the sub/present_file file must not be present"),
        ],
    )
    def test_file_absent(self, package_path, filepath, expect_error, assert_files_testdir):
        """Test the absent() method."""
        self._do_assert("absent", package_path, filepath, expect_error, assert_files_testdir)

    @pytest.mark.parametrize(
        "package_path, filepath, expect_error",
        [
            (".", "absent_dir", None),
            (".", "present_file", None),
            ("sub", "absent_dir", None),
            ("sub", "present_file", None),
            (".", "present_dir", "the present_dir directory must not be present"),
            ("sub", "present_dir", "the sub/present_dir directory must not be present"),
        ],
    )
    def test_dir_absent(self, package_path, filepath, expect_error, assert_files_testdir):
        """Test the dir_absent() method."""
        self._do_assert("dir_absent", package_path, filepath, expect_error, assert_files_testdir)

    @pytest.mark.parametrize("pkg_manager", ["npm", "yarn"])
    def test_different_pkg_managers(self, pkg_manager, assert_files_testdir):
        """Check that the pkg_manager value is, in fact, used for error messages."""
        af = utils.AssertPackageFiles(pkg_manager, assert_files_testdir)

        with pytest.raises(ValidationError, match=f"File check failed for {pkg_manager}"):
            af.present("absent_file")


@pytest.mark.parametrize("id, state", [(2, "stale"), (3, "complete"), (1, "in-progress")])
@mock.patch.object(requests_session, "get")
@mock.patch("cachito.workers.tasks.general.get_worker_config")
def test_get_request_state(mock_config, mock_requests_get, id, state):
    mock_requests_get.return_value.json.return_value = {"state": state}

    config = mock_config.return_value
    config.cachito_api_url = "http://cachito.domain.local/api/v1/"

    assert get_request_state(id) == state
    mock_requests_get.assert_called_once_with(f"http://cachito.domain.local/api/v1/requests/{id}")


@pytest.mark.parametrize(
    "id, state", [(1, "in_progress"), (2, "stale"), (3, "complete"), (None, "dummy")]
)
@mock.patch("cachito.workers.tasks.utils.get_request_state")
def test_runs_if_request_in_progress(mock_get_state, id, state):
    mock_get_state.return_value = state

    @utils.runs_if_request_in_progress
    def dummy_task(request_id):
        return 42

    if id is None:
        with pytest.raises(
            ValueError, match="Failed during state check: no request_id found for dummy_task task"
        ):
            dummy_task(id)
        mock_get_state.assert_not_called()
        return

    if state == "in_progress":
        assert dummy_task(id) == 42
    else:
        assert dummy_task(id) is None
    mock_get_state.assert_called_once_with(id)
