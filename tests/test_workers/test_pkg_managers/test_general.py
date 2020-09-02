# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

import requests
import pytest

from cachito.errors import CachitoError
from cachito.workers.pkg_managers.general import (
    download_binary_file,
    update_request_with_config_files,
    update_request_with_deps,
    update_request_with_package,
    verify_checksum,
    ChecksumInfo,
)


@mock.patch("cachito.workers.config.Config.cachito_deps_patch_batch_size", 5)
@mock.patch("cachito.workers.requests.requests_auth_session")
def test_update_request_with_deps(mock_requests, sample_deps_replace, sample_package):
    mock_requests.patch.return_value.ok = True
    update_request_with_deps(1, sample_package, sample_deps_replace)
    url = "http://cachito.domain.local/api/v1/requests/1"
    calls = [
        mock.call(
            url,
            json={"dependencies": sample_deps_replace[:5], "package": sample_package},
            timeout=60,
        ),
        mock.call(
            url,
            json={"dependencies": sample_deps_replace[5:10], "package": sample_package},
            timeout=60,
        ),
        mock.call(
            url,
            json={"dependencies": sample_deps_replace[10:], "package": sample_package},
            timeout=60,
        ),
    ]
    assert mock_requests.patch.call_count == 3
    mock_requests.patch.assert_has_calls(calls)


@mock.patch("cachito.workers.requests.requests_auth_session")
def test_update_request_with_package(mock_requests):
    mock_requests.patch.return_value.ok = True
    package = {
        "name": "helloworld",
        "type": "gomod",
        "version": "v0.0.0-20200324130456-8aedc0ec8bb5",
    }
    env_vars = {
        "GOCACHE": {"value": "deps/gomod", "kind": "path"},
        "GOPATH": {"value": "deps/gomod", "kind": "path"},
    }
    expected_json = {
        "environment_variables": env_vars,
        "package": package,
    }
    update_request_with_package(1, package, env_vars)
    mock_requests.patch.assert_called_once_with(
        "http://cachito.domain.local/api/v1/requests/1", json=expected_json, timeout=60
    )


@mock.patch("cachito.workers.requests.requests_auth_session")
def test_update_request_with_package_failed(mock_requests):
    mock_requests.patch.return_value.ok = False
    package = {
        "name": "helloworld",
        "type": "gomod",
        "version": "v0.0.0-20200324130456-8aedc0ec8bb5",
    }
    with pytest.raises(CachitoError, match="Setting a package on request 1 failed"):
        update_request_with_package(1, package)


@mock.patch("cachito.workers.requests.requests_auth_session")
def test_update_request_with_package_failed_connection(mock_requests):
    mock_requests.patch.side_effect = requests.ConnectTimeout()
    package = {
        "name": "helloworld",
        "type": "gomod",
        "version": "v0.0.0-20200324130456-8aedc0ec8bb5",
    }
    expected_msg = "The connection failed when adding a package to the request 1"
    with pytest.raises(CachitoError, match=expected_msg):
        update_request_with_package(1, package)


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


def test_verify_checksum(tmpdir):
    file = tmpdir.join("spells.txt")
    file.write("Beetlejuice! Beetlejuice! Beetlejuice!")

    expected = {
        "sha512": (
            "da518fe8b800b3325fe35ca680085fe37626414d0916937a01a25ef8f5d7aa769b7233073235fce85ee"
            "c717e02bb9d72062656cf2d79223792a784910c267b54"
        ),
        "sha256": "ed1f8cd69bfacf0528744b6a7084f36e8841b6128de0217503e215612a0ee835",
        "md5": "308764bc995153f7d853827a675e6731",
    }
    for algorithm, checksum in expected.items():
        verify_checksum(str(file), ChecksumInfo(algorithm, checksum))


def test_verify_checksum_invalid_hexdigest(tmpdir):
    file = tmpdir.join("spells.txt")
    file.write("Beetlejuice! Beetlejuice! Beetlejuice!")

    expected_error = "The file spells.txt has an unexpected checksum value"
    with pytest.raises(CachitoError, match=expected_error):
        verify_checksum(str(file), ChecksumInfo("sha512", "spam"))


def test_verify_checksum_unsupported_algorithm(tmpdir):
    file = tmpdir.join("spells.txt")
    file.write("Beetlejuice! Beetlejuice! Beetlejuice!")

    expected_error = "Cannot perform checksum on the file spells.txt,.*bacon.*"
    with pytest.raises(CachitoError, match=expected_error):
        verify_checksum(str(file), ChecksumInfo("bacon", "spam"))


@pytest.mark.parametrize("auth", [None, ("user", "password")])
@pytest.mark.parametrize("insecure", [True, False])
@pytest.mark.parametrize("chunk_size", [1024, 2048])
@mock.patch("cachito.workers.requests.requests_session")
def test_download_binary_file(mock_requests_session, auth, insecure, chunk_size, tmpdir):
    url = "http://example.org/example.tar.gz"
    content = b"file content"

    mock_response = mock_requests_session.get.return_value
    mock_response.iter_content.return_value = [content]

    download_path = tmpdir.join("example.tar.gz")
    download_binary_file(
        url, download_path.strpath, auth=auth, insecure=insecure, chunk_size=chunk_size
    )

    assert download_path.read_binary() == content
    mock_requests_session.get.assert_called_with(url, stream=True, auth=auth, verify=not insecure)
    mock_response.iter_content.assert_called_with(chunk_size=chunk_size)


@mock.patch("cachito.workers.requests.requests_session")
def test_download_binary_file_failed(mock_requests_session):
    mock_requests_session.get.side_effect = [requests.RequestException("Something went wrong")]

    expected = "Could not download http://example.org/example.tar.gz: Something went wrong"
    with pytest.raises(CachitoError, match=expected):
        download_binary_file("http://example.org/example.tar.gz", "/example.tar.gz")
