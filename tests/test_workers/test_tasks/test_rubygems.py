from unittest import mock

from cachito.workers.tasks import rubygems


@mock.patch("cachito.workers.tasks.rubygems.nexus.execute_script")
def test_cleanup_rubygems_request(mock_exec_script):
    rubygems.cleanup_rubygems_request(42)

    expected_payload = {
        "rubygems_repository_name": "cachito-rubygems-hosted-42",
        "username": "cachito-rubygems-42",
    }
    mock_exec_script.assert_called_once_with("rubygems_cleanup", expected_payload)
