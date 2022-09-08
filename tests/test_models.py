# SPDX-License-Identifier: GPL-3.0-or-later
import datetime
from typing import Optional
from unittest import mock

import pytest

from cachito.web.models import PackageManager, Request, RequestStateMapping


@pytest.mark.parametrize(
    "name,expected",
    [["gomod", "gomod"], ["", None], [None, None], ["unknown", None]],
)
def test_package_manager_get_by_name(name, expected, app, db, auth_env):
    pkg_manager: Optional[PackageManager] = PackageManager.get_by_name(name)
    if expected is None:
        assert expected == pkg_manager
    else:
        assert expected == pkg_manager.name


class TestRequest:
    def _create_request_object(self):
        request = Request()
        request.repo = "a_repo"
        request.ref = "a_ref"
        request.user_id = 1
        request.submitted_by_id = 1
        request.packages_count = 1
        request.dependencies_count = 1

        return request

    @pytest.mark.parametrize(
        "state, call_count",
        [
            [RequestStateMapping.in_progress.name, 0],
            [RequestStateMapping.complete.name, 2],
            [RequestStateMapping.failed.name, 0],
            [RequestStateMapping.stale.name, 0],
        ],
    )
    @mock.patch("cachito.common.packages_data.PackagesData.load")
    def test_package_data_is_only_accessed_when_request_is_complete(
        self, load_mock, state, call_count, app, auth_env
    ):
        request = self._create_request_object()
        request.add_state(state, "Reason")

        with app.test_request_context(environ_base=auth_env):
            request.to_json()
            request.content_manifest.to_json()

        assert load_mock.call_count == call_count

    def test_utcnow(self, app, auth_env):
        request = self._create_request_object()
        request.add_state(RequestStateMapping.complete.name, "Complete")
        current_utc_datetime = datetime.datetime.utcnow()
        request_created_datetime = request.created
        diff_in_secs = (current_utc_datetime - request_created_datetime).total_seconds()

        # Difference between "created" and current UTC datetimes is within 10 seconds
        assert abs(diff_in_secs) <= 10
