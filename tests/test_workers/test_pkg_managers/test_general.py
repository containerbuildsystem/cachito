# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

import requests
import pytest

from cachito.errors import CachitoError
from cachito.workers.pkg_managers.general import (
    update_request_with_config_files,
    update_request_with_deps,
    update_request_with_packages,
)


@mock.patch("cachito.workers.config.Config.cachito_deps_patch_batch_size", 5)
@mock.patch("cachito.workers.requests.requests_auth_session")
def test_update_request_with_deps(mock_requests, sample_deps_replace):
    mock_requests.patch.return_value.ok = True
    update_request_with_deps(1, sample_deps_replace)
    url = "http://cachito.domain.local/api/v1/requests/1"
    calls = [
        mock.call(url, json={"dependencies": sample_deps_replace[:5]}, timeout=60),
        mock.call(url, json={"dependencies": sample_deps_replace[5:10]}, timeout=60),
        mock.call(url, json={"dependencies": sample_deps_replace[10:]}, timeout=60),
    ]
    assert mock_requests.patch.call_count == 3
    mock_requests.patch.assert_has_calls(calls)


@mock.patch("cachito.workers.requests.requests_auth_session")
def test_update_request_with_packages(mock_requests):
    mock_requests.patch.return_value.ok = True
    packages = [
        {"name": "helloworld", "type": "gomod", "version": "v0.0.0-20200324130456-8aedc0ec8bb5"}
    ]
    pkg_manager = "gomod"
    env_vars = {"GOCACHE": "deps/gomod", "GOPATH": "deps/gomod"}
    expected_json = {
        "environment_variables": env_vars,
        "packages": packages,
        "pkg_managers": [pkg_manager],
    }
    update_request_with_packages(1, packages, pkg_manager, env_vars)
    mock_requests.patch.assert_called_once_with(
        "http://cachito.domain.local/api/v1/requests/1", json=expected_json, timeout=60
    )


@mock.patch("cachito.workers.requests.requests_auth_session")
def test_update_request_with_packages_failed(mock_requests):
    mock_requests.patch.return_value.ok = False
    packages = [
        {"name": "helloworld", "type": "gomod", "version": "v0.0.0-20200324130456-8aedc0ec8bb5"}
    ]
    with pytest.raises(CachitoError, match="Setting the packages on request 1 failed"):
        update_request_with_packages(1, packages)


@mock.patch("cachito.workers.requests.requests_auth_session")
def test_update_request_with_packages_failed_connection(mock_requests):
    mock_requests.patch.side_effect = requests.ConnectTimeout()
    packages = [
        {"name": "helloworld", "type": "gomod", "version": "v0.0.0-20200324130456-8aedc0ec8bb5"}
    ]
    expected_msg = "The connection failed when adding packages to the request 1"
    with pytest.raises(CachitoError, match=expected_msg):
        update_request_with_packages(1, packages)


@mock.patch("cachito.workers.requests.requests_auth_session")
def test_update_request_with_config_files(mock_requests):
    mock_requests.post.return_value.ok = True

    config_files = [
        {
            "content": "U3RyYW5nZSB0aGluZ3MgYXJlIGhhcHBlbmluZyB0byBtZQo=",
            "path": "app/mystery",
            "type": "base64",
        }
    ]
    update_request_with_config_files(1, config_files)

    mock_requests.post.assert_called_once()
    assert mock_requests.post.call_args[0][0].endswith("/api/v1/requests/1/configuration-files")


@mock.patch("cachito.workers.requests.requests_auth_session")
def test_update_request_with_config_files_failed_connection(mock_requests):
    mock_requests.post.side_effect = requests.ConnectionError()

    expected = "The connection failed when adding configuration files to the request 1"
    with pytest.raises(CachitoError, match=expected):
        update_request_with_config_files(1, [])


@mock.patch("cachito.workers.requests.requests_auth_session")
def test_update_request_with_config_files_failed(mock_requests):
    mock_requests.post.return_value.ok = False

    expected = "Adding configuration files on request 1 failed"
    with pytest.raises(CachitoError, match=expected):
        update_request_with_config_files(1, [])
