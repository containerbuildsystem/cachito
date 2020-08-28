# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

from cachito.workers.tasks import pip


@mock.patch("cachito.workers.tasks.pip.nexus.execute_script")
def test_cleanup_pip_request(mock_exec_script):
    pip.cleanup_pip_request(42)

    expected_payload = {
        "pip_repository_name": "cachito-pip-hosted-42",
        "raw_repository_name": "cachito-pip-raw-42",
        "username": "cachito-pip-42",
    }
    mock_exec_script.assert_called_once_with("pip_cleanup", expected_payload)
