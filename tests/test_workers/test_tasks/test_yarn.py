# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

from cachito.workers.tasks import yarn


@mock.patch("cachito.workers.tasks.yarn.nexus.execute_script")
def test_cleanup_yarn_request(mock_exec_script):
    yarn.cleanup_yarn_request(42)

    expected_payload = {
        "repository_name": "cachito-yarn-42",
        "username": "cachito-yarn-42",
    }
    mock_exec_script.assert_called_once_with("js_cleanup", expected_payload)
