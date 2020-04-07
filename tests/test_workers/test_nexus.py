# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

import requests
import pytest

from cachito.errors import CachitoError
from cachito.workers import nexus
from cachito.workers.errors import NexusScriptError


@mock.patch("cachito.workers.requests.requests_session")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="println('Hello')"))
def test_create_or_update_create(mock_requests):
    mock_get = mock.Mock()
    mock_get.status_code = 404
    mock_post = mock.Mock()
    mock_post.ok = True
    mock_requests.request.side_effect = [mock_get, mock_post]

    nexus.create_or_update_script("oh_so", "/it/is/oh_so.groovy")

    assert mock_requests.request.call_count == 2
    request_calls = mock_requests.request.call_args_list
    assert request_calls[0][0][0] == "get"
    assert request_calls[1][0][0] == "post"


@mock.patch("cachito.workers.requests.requests_session")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="println('Hello')"))
def test_create_or_update_update(mock_requests):
    mock_get = mock.Mock()
    mock_get.status_code = 200
    mock_get.json.return_value = {"content": "println('Goodbye')"}
    mock_put = mock.Mock()
    mock_put.ok = True
    mock_requests.request.side_effect = [mock_get, mock_put]

    nexus.create_or_update_script("oh_so", "/it/is/oh_so.groovy")

    assert mock_requests.request.call_count == 2
    request_calls = mock_requests.request.call_args_list
    assert request_calls[0][0][0] == "get"
    assert request_calls[1][0][0] == "put"


@mock.patch("cachito.workers.requests.requests_session")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="println('Hello')"))
def test_create_or_update_already_set(mock_requests):
    mock_get = mock.Mock()
    mock_get.status_code = 200
    mock_get.json.return_value = {"content": "println('Hello')"}
    mock_requests.request.side_effect = [mock_get]

    nexus.create_or_update_script("oh_so", "/it/is/oh_so.groovy")

    assert mock_requests.request.call_count == 1
    assert mock_requests.request.call_args[0][0] == "get"


@mock.patch("cachito.workers.requests.requests_session")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="println('Hello')"))
def test_create_or_update_get_fails(mock_requests):
    mock_get = mock.Mock()
    mock_get.status_code = 400
    mock_requests.request.side_effect = [mock_get]

    expected = "Failed to determine if the Nexus script oh_so exists"
    with pytest.raises(CachitoError, match=expected):
        nexus.create_or_update_script("oh_so", "/it/is/oh_so.groovy")

    assert mock_requests.request.call_count == 1
    assert mock_requests.request.call_args[0][0] == "get"


@mock.patch("cachito.workers.requests.requests_session")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="println('Hello')"))
def test_create_or_update_get_connection_error(mock_requests):
    mock_requests.request.side_effect = requests.ConnectionError()

    expected = "The connection failed when determining if the Nexus script oh_so exists"
    with pytest.raises(CachitoError, match=expected):
        nexus.create_or_update_script("oh_so", "/it/is/oh_so.groovy")

    assert mock_requests.request.call_count == 1
    assert mock_requests.request.call_args[0][0] == "get"


@mock.patch("cachito.workers.requests.requests_session")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="println('Hello')"))
def test_create_or_update_create_fails(mock_requests):
    mock_get = mock.Mock()
    mock_get.status_code = 404
    mock_post = mock.Mock()
    mock_post.ok = False
    mock_requests.request.side_effect = [mock_get, mock_post]

    expected = "Failed to create/update the Nexus script oh_so"
    with pytest.raises(CachitoError, match=expected):
        nexus.create_or_update_script("oh_so", "/it/is/oh_so.groovy")

    assert mock_requests.request.call_count == 2
    request_calls = mock_requests.request.call_args_list
    assert request_calls[0][0][0] == "get"
    assert request_calls[1][0][0] == "post"


@mock.patch("cachito.workers.requests.requests_session")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="println('Hello')"))
def test_create_or_update_update_fails(mock_requests):
    mock_get = mock.Mock()
    mock_get.status_code = 200
    mock_get.json.return_value = {"content": "println('Goodby')"}
    mock_put = mock.Mock()
    mock_put.ok = False
    mock_requests.request.side_effect = [mock_get, mock_put]

    expected = "Failed to create/update the Nexus script oh_so"
    with pytest.raises(CachitoError, match=expected):
        nexus.create_or_update_script("oh_so", "/it/is/oh_so.groovy")

    assert mock_requests.request.call_count == 2
    request_calls = mock_requests.request.call_args_list
    assert request_calls[0][0][0] == "get"
    assert request_calls[1][0][0] == "put"


@mock.patch("cachito.workers.nexus.create_or_update_script")
def test_create_or_update_scripts(mock_cous):
    nexus.create_or_update_scripts()
    expected_scripts = {"js_after_content_staged", "js_before_content_staged", "js_cleanup"}
    for call_args in mock_cous.call_args_list:
        script_name = call_args[0][0]
        expected_scripts.remove(script_name)

    error_msg = f"The following scripts were missed: {', '.join(expected_scripts)}"
    assert len(expected_scripts) == 0, error_msg


@mock.patch("cachito.workers.requests.requests_session")
def test_execute_script(mock_requests):
    mock_requests.post.return_value.ok = True

    nexus.execute_script(
        "js_cleanup", {"repository_name": "cachito-js-1", "username": "cachito-js-1"}
    )

    mock_requests.post.assert_called_once()
    assert mock_requests.post.call_args[0][0].endswith("/service/rest/v1/script/js_cleanup/run")


@mock.patch("cachito.workers.requests.requests_session")
def test_execute_script_connection_error(mock_requests):
    mock_requests.post.side_effect = requests.ConnectionError

    expected = "Could not connect to the Nexus instance to execute the script js_cleanup"
    with pytest.raises(NexusScriptError, match=expected):
        nexus.execute_script(
            "js_cleanup", {"repository_name": "cachito-js-1", "username": "cachito-js-1"}
        )

    mock_requests.post.assert_called_once()


@mock.patch("cachito.workers.requests.requests_session")
def test_execute_script_failed(mock_requests):
    mock_requests.post.return_value.ok = False
    mock_requests.post.return_value.text = "some error"

    expected = "The Nexus script js_cleanup failed with: some error"
    with pytest.raises(NexusScriptError, match=expected):
        nexus.execute_script(
            "js_cleanup", {"repository_name": "cachito-js-1", "username": "cachito-js-1"}
        )

    mock_requests.post.assert_called_once()


@mock.patch("cachito.workers.nexus.os.path.exists")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="some CA cert"))
def test_get_ca_cert_exists(mock_exists):
    mock_exists.return_value = True

    assert nexus.get_ca_cert() == "some CA cert"
