# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
from unittest import mock

import pytest
import requests

from cachito.errors import ValidationError, CachitoError
from cachito.workers.tasks import utils
from cachito.workers.requests import requests_session

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


@mock.patch("cachito.workers.tasks.utils._get_request_or_fail")
def test_get_request(mock_get_request_or_fail):
    mock_get_request_or_fail.return_value = {"id": 42, "state": "complete"}

    assert utils.get_request(42) == {"id": 42, "state": "complete"}
    mock_get_request_or_fail.assert_called_once_with(
        42,
        connect_error_msg="The connection failed while getting request 42: {exc}",
        status_error_msg="Failed to get request 42: {exc}",
    )


@pytest.mark.parametrize("id, state", [(2, "stale"), (3, "complete"), (1, "in-progress")])
@mock.patch("cachito.workers.tasks.utils._get_request_or_fail")
def test_get_request_state(mock_get_request_or_fail, id, state):
    mock_get_request_or_fail.return_value = {"state": state}

    assert utils.get_request_state(id) == state
    mock_get_request_or_fail.assert_called_once_with(
        id,
        connect_error_msg=f"The connection failed while getting the state of request {id}: {{exc}}",
        status_error_msg=f"Failed to get the state of request {id}: {{exc}}",
    )


@pytest.mark.parametrize(
    "connect_error, status_error, expect_error",
    [
        (None, None, None),
        (
            requests.ConnectionError("connection failed"),
            None,
            "connection error: connection failed",
        ),
        (requests.Timeout("timed out"), None, "connection error: timed out",),
        (
            None,
            requests.HTTPError("404 Client Error: NOT FOUND"),
            "status error: 404 Client Error: NOT FOUND",
        ),
    ],
)
@mock.patch.object(requests_session, "get")
@mock.patch("cachito.workers.tasks.general.get_worker_config")
def test_get_request_or_fail(
    mock_config, mock_requests_get, connect_error, status_error, expect_error
):
    config = mock_config.return_value
    config.cachito_api_url = "http://cachito.domain.local/api/v1/"
    config.cachito_api_timeout = 60

    if connect_error:
        mock_requests_get.side_effect = [connect_error]

    response = mock_requests_get.return_value
    if status_error:
        response.raise_for_status.side_effect = [status_error]

    response.json.return_value = {"id": 42, "state": "complete"}

    if expect_error:
        with pytest.raises(CachitoError, match=expect_error):
            utils._get_request_or_fail(42, "connection error: {exc}", "status error: {exc}")
    else:
        request = utils._get_request_or_fail(42, "connection error: {exc}", "status error: {exc}")
        assert request == {"id": 42, "state": "complete"}

    mock_requests_get.assert_called_once_with(
        "http://cachito.domain.local/api/v1/requests/42", timeout=60,
    )


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
