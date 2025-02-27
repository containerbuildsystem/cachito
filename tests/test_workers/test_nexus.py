# SPDX-License-Identifier: GPL-3.0-or-later
import copy
from unittest import mock

import pytest
import requests

from cachito.errors import NetworkError, NexusError
from cachito.workers import nexus
from cachito.workers.errors import NexusScriptError, UploadError


def setup_module():
    """Disable logging."""
    nexus.log.disabled = True


@pytest.fixture()
def components_search_results():
    return {
        "items": [
            {
                "id": "Y2FjaGl0by1qcy1ob3N0ZWQ6MTNiMjllNDQ5ZjBlM2I4ZDM5OTY0ZWQzZTExMGUyZTM",
                "repository": "cachito-js-hosted",
                "format": "npm",
                "group": "",
                "name": "rxjs",
                "version": "7.0.0-beta.0-external-dfa239d41b97504312fa95e13f4d593d95b49c4b",
                "assets": [
                    {
                        "downloadUrl": (
                            "http://nexus/repository/cachito-js-hosted/rxjs/-/rxjs-7.0.0-beta.0"
                            "-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b.tgz"
                        ),
                        "path": (
                            "rxjs/-/rxjs-7.0.0-beta.0-external-gitcommit-"
                            "dfa239d41b97504312fa95e13f4d593d95b49c4b.tgz"
                        ),
                        "id": "Y2FjaGl0by1qcy1ob3N0ZWQ6Mjk0YzYzMzJiNDlhNmQ0NTY3MmM5YmNhZDg0YWI2ZTM",
                        "repository": "cachito-js-hosted",
                        "format": "npm",
                        "checksum": {
                            "sha1": "7500bf7b05fb79a85b2c10c4aed0550ee57f0d87",
                            "sha512": (
                                "144d7633612bf4e46422557cd72be605af8f86249e87f6585a447622746cfcfbc"
                                "d05aff81f5786368b9ff377d0bb08a05b363b8da82ea797e38795c497fc70e7"
                            ),
                            "sha256": (
                                "78b4e698935ebb54e958fb92646e0cad2effcbfdb36be06f491b033779cd0fdf"
                            ),
                            "md5": "8e5b6513036e0de8a6e9f40e3a7a386e",
                        },
                    }
                ],
            },
            {
                "id": "Y2FjaGl0by1qcy1ob3N0ZWQ6ZDQ4MTE3NTQxZGNiODllYzYxM2IyMzk3MzIwMWQ3YmE",
                "repository": "cachito-js-hosted",
                "format": "npm",
                "group": "reactivex",
                "name": "rxjs",
                "version": "6.5.5-external-gitcommit-78032157f5c1655436829017bbda787565b48c30",
                "assets": [
                    {
                        "downloadUrl": (
                            "http://nexus/repository/cachito-js-hosted/@reactivex/rxjs/-/"
                            "rxjs-6.5.5-external-gitcommit-"
                            "78032157f5c1655436829017bbda787565b48c30.tgz"
                        ),
                        "path": (
                            "@reactivex/rxjs/-/rxjs-6.5.5-external-gitcommit-"
                            "78032157f5c1655436829017bbda787565b48c30.tgz"
                        ),
                        "id": "Y2FjaGl0by1qcy1ob3N0ZWQ6Yzc1NDU1NzlhN2ExNTM5MDI5YmRiOTI4YzdkNGFiZDQ",
                        "repository": "cachito-js-hosted",
                        "format": "npm",
                        "checksum": {
                            "sha1": "3d125fa8fda499a6405da48d14796607041b7ed7",
                            "sha512": (
                                "7cda573352bb69f9aee6bbe31875e3bfb6978faf1551cc41f6bda31331ffcbade"
                                "48cf38d2bf621346c09ba35bc0d023d26acdbe82167e934e73d646c7fae4286"
                            ),
                            "sha256": (
                                "0b1bd5838ec06d6e26064a958636559eb9ecb8fe11722e703c90d4becf9265a2"
                            ),
                            "md5": "1977decdef38174ce256927687dd197d",
                        },
                    }
                ],
            },
        ],
        "continuationToken": None,
    }


@pytest.mark.parametrize(
    "hoster_username, username, hoster_password, password, expected_username, expected_password",
    (
        (None, "cachito", None, "cachito", "cachito", "cachito"),
        ("cachito-uploader", "cachito", None, "cachito", "cachito-uploader", "cachito"),
        (
            "cachito-uploader",
            "cachito",
            None,
            "cachito-password",
            "cachito-uploader",
            "cachito-password",
        ),
        (None, "cachito", "cachito-password", "cachito", "cachito", "cachito-password"),
    ),
)
@mock.patch("cachito.workers.nexus.get_worker_config")
def test_get_nexus_hoster_credentials(
    mock_gwc,
    hoster_username,
    username,
    hoster_password,
    password,
    expected_username,
    expected_password,
):
    mock_gwc.return_value.cachito_nexus_hoster_username = hoster_username
    mock_gwc.return_value.cachito_nexus_hoster_password = hoster_password
    mock_gwc.return_value.cachito_nexus_username = username
    mock_gwc.return_value.cachito_nexus_password = password

    rv_username, rv_password = nexus.get_nexus_hoster_credentials()

    assert rv_username == expected_username
    assert rv_password == expected_password


@pytest.mark.parametrize(
    "cachito_nexus_hoster_url, cachito_nexus_url, expected",
    (
        ("http://hoster", "http://managed", "http://hoster"),
        (None, "http://managed", "http://managed"),
    ),
)
@mock.patch("cachito.workers.nexus.get_worker_config")
def test_get_nexus_hoster_url(mock_gwc, cachito_nexus_hoster_url, cachito_nexus_url, expected):
    mock_gwc.return_value.cachito_nexus_hoster_url = cachito_nexus_hoster_url
    mock_gwc.return_value.cachito_nexus_url = cachito_nexus_url

    assert nexus._get_nexus_hoster_url() == expected


@mock.patch.object(nexus.nexus_requests_session, "request")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="println('Hello')"))
def test_create_or_update_create(mock_request):
    mock_get = mock.Mock()
    mock_get.status_code = 404
    mock_post = mock.Mock()
    mock_post.ok = True
    mock_request.side_effect = [mock_get, mock_post]

    nexus.create_or_update_script("oh_so", "/it/is/oh_so.groovy")

    assert mock_request.call_count == 2
    request_calls = mock_request.call_args_list
    assert request_calls[0][0][0] == "get"
    assert request_calls[1][0][0] == "post"


@mock.patch.object(nexus.nexus_requests_session, "request")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="println('Hello')"))
def test_create_or_update_update(mock_request):
    mock_get = mock.Mock()
    mock_get.status_code = 200
    mock_get.json.return_value = {"content": "println('Goodbye')"}
    mock_put = mock.Mock()
    mock_put.ok = True
    mock_request.side_effect = [mock_get, mock_put]

    nexus.create_or_update_script("oh_so", "/it/is/oh_so.groovy")

    assert mock_request.call_count == 2
    request_calls = mock_request.call_args_list
    assert request_calls[0][0][0] == "get"
    assert request_calls[1][0][0] == "put"


@mock.patch.object(nexus.nexus_requests_session, "request")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="println('Hello')"))
def test_create_or_update_already_set(mock_request):
    mock_get = mock.Mock()
    mock_get.status_code = 200
    mock_get.json.return_value = {"content": "println('Hello')"}
    mock_request.side_effect = [mock_get]

    nexus.create_or_update_script("oh_so", "/it/is/oh_so.groovy")

    assert mock_request.call_count == 1
    assert mock_request.call_args[0][0] == "get"


@mock.patch.object(nexus.nexus_requests_session, "request")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="println('Hello')"))
def test_create_or_update_get_fails(mock_request):
    mock_get = mock.Mock()
    mock_get.status_code = 400
    mock_request.side_effect = [mock_get]

    expected = "Failed to determine if the Nexus script oh_so exists"
    with pytest.raises(NetworkError, match=expected):
        nexus.create_or_update_script("oh_so", "/it/is/oh_so.groovy")

    assert mock_request.call_count == 1
    assert mock_request.call_args[0][0] == "get"


@mock.patch.object(nexus.nexus_requests_session, "request")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="println('Hello')"))
def test_create_or_update_get_connection_error(mock_request):
    mock_request.side_effect = requests.ConnectionError()

    expected = "The connection failed when determining if the Nexus script oh_so exists"
    with pytest.raises(NetworkError, match=expected):
        nexus.create_or_update_script("oh_so", "/it/is/oh_so.groovy")

    assert mock_request.call_count == 1
    assert mock_request.call_args[0][0] == "get"


@mock.patch.object(nexus.nexus_requests_session, "request")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="println('Hello')"))
def test_create_or_update_create_fails(mock_request):
    mock_get = mock.Mock()
    mock_get.status_code = 404
    mock_post = mock.Mock()
    mock_post.ok = False
    mock_request.side_effect = [mock_get, mock_post]

    expected = "Failed to create/update the Nexus script oh_so"
    with pytest.raises(NetworkError, match=expected):
        nexus.create_or_update_script("oh_so", "/it/is/oh_so.groovy")

    assert mock_request.call_count == 2
    request_calls = mock_request.call_args_list
    assert request_calls[0][0][0] == "get"
    assert request_calls[1][0][0] == "post"


@mock.patch.object(nexus.nexus_requests_session, "request")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="println('Hello')"))
def test_create_or_update_update_fails(mock_request):
    mock_get = mock.Mock()
    mock_get.status_code = 200
    mock_get.json.return_value = {"content": "println('Goodby')"}
    mock_put = mock.Mock()
    mock_put.ok = False
    mock_request.side_effect = [mock_get, mock_put]

    expected = "Failed to create/update the Nexus script oh_so"
    with pytest.raises(NetworkError, match=expected):
        nexus.create_or_update_script("oh_so", "/it/is/oh_so.groovy")

    assert mock_request.call_count == 2
    request_calls = mock_request.call_args_list
    assert request_calls[0][0][0] == "get"
    assert request_calls[1][0][0] == "put"


@mock.patch("cachito.workers.nexus.create_or_update_script")
def test_create_or_update_scripts(mock_cous):
    nexus.create_or_update_scripts()
    expected_scripts = {
        "js_after_content_staged",
        "js_before_content_staged",
        "js_cleanup",
        "pip_after_content_staged",
        "pip_before_content_staged",
        "pip_cleanup",
        "rubygems_before_content_staged",
        "rubygems_after_content_staged",
        "rubygems_cleanup",
    }
    for call_args in mock_cous.call_args_list:
        script_name = call_args[0][0]
        expected_scripts.remove(script_name)

    error_msg = f"The following scripts were missed: {', '.join(expected_scripts)}"
    assert len(expected_scripts) == 0, error_msg


@mock.patch.object(nexus.nexus_requests_session, "post")
def test_execute_script(mock_post):
    mock_post.return_value.ok = True

    nexus.execute_script(
        "js_cleanup", {"repository_name": "cachito-npm-1", "username": "cachito-npm-1"}
    )

    mock_post.assert_called_once()
    assert mock_post.call_args[0][0].endswith("/service/rest/v1/script/js_cleanup/run")


@mock.patch.object(nexus.nexus_requests_session, "post")
def test_execute_script_connection_error(mock_post):
    mock_post.side_effect = requests.ConnectionError

    expected = "Could not connect to the Nexus instance to execute the script js_cleanup"
    with pytest.raises(NexusScriptError, match=expected):
        nexus.execute_script(
            "js_cleanup", {"repository_name": "cachito-npm-1", "username": "cachito-npm-1"}
        )

    mock_post.assert_called_once()


@mock.patch.object(nexus.nexus_requests_session, "post")
def test_execute_script_failed(mock_post):
    mock_post.return_value.ok = False
    mock_post.return_value.text = "some error"

    expected = "The Nexus script js_cleanup failed with: some error"
    with pytest.raises(NexusScriptError, match=expected):
        nexus.execute_script(
            "js_cleanup", {"repository_name": "cachito-npm-1", "username": "cachito-npm-1"}
        )

    mock_post.assert_called_once()


@mock.patch("cachito.workers.nexus.os.path.exists")
@mock.patch("cachito.workers.nexus.open", mock.mock_open(read_data="some CA cert"))
def test_get_ca_cert_exists(mock_exists):
    mock_exists.return_value = True

    assert nexus.get_ca_cert() == "some CA cert"


@mock.patch("cachito.workers.nexus.search_components")
def test_get_component_info_from_nexus(mock_search_components, components_search_results):
    repository = "cachito-js-proxy"
    component_format = "npm"
    name = "rxjs"
    version = "6.5.5-external-gitcommit-78032157f5c1655436829017bbda787565b48c30"
    group = "reactive"
    components_search_results["items"].pop(0)
    mock_search_components.return_value = components_search_results["items"]

    results = nexus.get_component_info_from_nexus(
        repository, component_format, name, version, group=group
    )

    expected = components_search_results["items"][0]
    assert results == expected


@mock.patch("cachito.workers.nexus.time.sleep")
@mock.patch("cachito.workers.nexus.search_components")
def test_get_component_info_from_nexus_no_results(mock_search_components, mock_sleep):
    mock_search_components.return_value = []

    results = nexus.get_component_info_from_nexus(
        "cachito-js-proxy", "npm", "rxjs", "6.55.5", max_attempts=3
    )

    assert results is None
    assert mock_sleep.call_count == 2
    assert mock_search_components.call_count == 3


@mock.patch("cachito.workers.nexus.search_components")
def test_get_component_info_from_nexus_null_group(mock_search_components):
    mock_search_components.return_value = [
        {"name": "foo", "group": "some", "version": "1.0.0"},
        {"name": "foo", "group": "", "version": "1.0.0"},
    ]

    result = nexus.get_component_info_from_nexus(
        "cachito-js-proxy", "npm", "foo", version="1.0.0", group=nexus.NULL_GROUP
    )

    assert result == mock_search_components.return_value[1]


@mock.patch("cachito.workers.nexus.search_components")
def test_get_component_info_from_nexus_any_group(mock_search_components):
    mock_search_components.return_value = [
        {"name": "foo", "group": "some", "version": "1.0.0"},
    ]

    result = nexus.get_component_info_from_nexus(
        "cachito-js-proxy", "npm", "foo", version="1.0.0", group=None
    )

    assert result == mock_search_components.return_value[0]


@mock.patch("cachito.workers.nexus.search_components")
def test_get_component_info_from_nexus_multiple_results(
    mock_search_components, components_search_results
):
    mock_search_components.return_value = components_search_results["items"]

    expected = "The component search in Nexus unexpectedly returned more than one result"
    with pytest.raises(NexusError, match=expected):
        nexus.get_component_info_from_nexus("cachito-js-proxy", "npm", "rxjs", "*")


@pytest.mark.parametrize("raw, version", [(True, "some"), (False, None)])
def test_get_component_info_from_nexus_version_vs_raw(raw, version):
    component_format = "raw" if raw else "npm"

    expected = "'version' argument must be provided if and only if format is not 'raw'"
    with pytest.raises(ValueError, match=expected):
        nexus.get_component_info_from_nexus(
            "some-repository", component_format, "some-name", version
        )


@pytest.mark.parametrize(
    "hoster, hoster_is_orient_db, proxy_is_orient_db, treated_name",
    [
        pytest.param(True, True, False, "foo/bar/foobar-1.0.tar.gz", id="from_hoster_is_orient_db"),
        pytest.param(False, False, True, "foo/bar/foobar-1.0.tar.gz", id="from_proxy_is_orient_db"),
        pytest.param(
            False, False, False, "/foo/bar/foobar-1.0.tar.gz", id="from_proxy_is_not_orient_db"
        ),
        pytest.param(
            True, False, True, "/foo/bar/foobar-1.0.tar.gz", id="from_hoster_is_not_orient_db"
        ),
    ],
)
@mock.patch("cachito.workers.nexus.get_component_info_from_nexus")
@mock.patch("cachito.workers.nexus._get_nexus_orient_db_configuration")
def test_get_raw_component_asset_url(
    mock_get_nexus_orient_db_configuration,
    mock_get_component_info,
    hoster,
    hoster_is_orient_db,
    proxy_is_orient_db,
    treated_name,
):
    mock_get_component_info.return_value = {
        # "id": "1234",
        # "repository": "cachito-pip-raw",
        # "format": "raw",
        # "name": "foo/bar/foobar-1.0.tar.gz",
        # "version": None,
        "assets": [
            {
                "downloadUrl": "http://nexus/repository/cachito-pip-raw/foo/bar/foobar-1.0.tar.gz",
                # "path": "foo/bar/foobar-1.0.tar.gz",
                # "id": "5678",
                # "repository": "cachito-pip-raw",
                # "format": "raw",
                # "checksum": {
                #     "sha1": "abcdef",
                #     "sha512": "123456",
                #     "sha256": "fedcba",
                #     "md5": "654321",
                # }
            }
        ]
    }

    mock_get_nexus_orient_db_configuration.return_value = (hoster_is_orient_db, proxy_is_orient_db)

    url = nexus.get_raw_component_asset_url(
        "cachito-pip-raw", "/foo/bar/foobar-1.0.tar.gz", from_nexus_hoster=hoster
    )
    assert url == "http://nexus/repository/cachito-pip-raw/foo/bar/foobar-1.0.tar.gz"

    mock_get_component_info.assert_called_once_with(
        "cachito-pip-raw",
        "raw",
        treated_name,
        max_attempts=1,
        from_nexus_hoster=hoster,
    )


@mock.patch("cachito.workers.nexus.get_component_info_from_nexus")
def test_get_raw_component_asset_url_not_found(mock_get_component_info):
    mock_get_component_info.return_value = None

    url = nexus.get_raw_component_asset_url("cachito-pip-raw", "foo/bar/foobar-1.0.tar.gz")
    assert url is None


@pytest.mark.parametrize(
    "component, error",
    [
        ({"assets": []}, "Component foo/bar/foobar-1.0.tar.gz has no assets"),
        ({"assets": [1, 2]}, "Component foo/bar/foobar-1.0.tar.gz has more than 1 asset"),
    ],
)
@mock.patch("cachito.workers.nexus.get_component_info_from_nexus")
def test_get_raw_component_asset_url_sanity_check(mock_get_component_info, component, error):
    mock_get_component_info.return_value = component

    with pytest.raises(RuntimeError, match=error):
        nexus.get_raw_component_asset_url("cachito-pip-raw", "foo/bar/foobar-1.0.tar.gz")


@mock.patch.object(nexus.nexus_requests_session, "get")
def test_search_components(mock_get, components_search_results):
    # Split up the components_search_results fixture into two pages to test pagination
    first_page = copy.deepcopy(components_search_results)
    first_page["items"].pop(1)
    first_page["continuationToken"] = "someToken"
    mock_rv_first = mock.Mock()
    mock_rv_first.ok = True
    mock_rv_first.json.return_value = first_page

    second_page = copy.deepcopy(components_search_results)
    second_page["items"].pop(0)
    mock_rv_second = mock.Mock()
    mock_rv_second.ok = True
    mock_rv_second.json.return_value = second_page

    mock_get.side_effect = [mock_rv_first, mock_rv_second]

    results = nexus.search_components(repository="cachito-js-hosted", type="npm")

    assert results == components_search_results["items"]

    assert mock_get.call_count == 2


@pytest.mark.parametrize("hoster", [True, False])
@mock.patch.object(nexus.nexus_requests_session, "get")
@mock.patch("requests.auth.HTTPBasicAuth")
@mock.patch("cachito.workers.nexus.get_worker_config")
def test_search_components_auth(mock_gwc, mock_auth, mock_get, components_search_results, hoster):
    hoster_credential = "hoster"
    local_credential = "local"
    mock_gwc.return_value.cachito_nexus_hoster_username = hoster_credential
    mock_gwc.return_value.cachito_nexus_hoster_password = hoster_credential
    mock_gwc.return_value.cachito_nexus_username = local_credential
    mock_gwc.return_value.cachito_nexus_password = local_credential

    mock_rv = mock.Mock()
    mock_rv.ok = True
    mock_rv.json.return_value = components_search_results
    mock_get.return_value = mock_rv
    results = nexus.search_components(
        in_nexus_hoster=hoster, repository="cachito-js-hosted", type="npm"
    )

    assert results == components_search_results["items"]
    assert mock_get.call_count == 1
    if hoster:
        mock_auth.assert_called_once_with(hoster_credential, hoster_credential)
    else:
        mock_auth.assert_called_once_with(local_credential, local_credential)


@mock.patch.object(nexus.nexus_requests_session, "get")
def test_search_components_connection_error(mock_get):
    mock_get.side_effect = requests.ConnectionError()

    expected = "Could not connect to the Nexus instance to search for components"
    with pytest.raises(NetworkError, match=expected):
        nexus.search_components(repository="cachito-js-hosted", type="npm")


@mock.patch.object(nexus.nexus_requests_session, "get")
def test_search_components_failed(mock_get):
    mock_get.return_value.ok = False

    expected = "Failed to search for components in Nexus"
    with pytest.raises(NexusError, match=expected):
        nexus.search_components(repository="cachito-js-hosted", type="npm")


@mock.patch.object(nexus.nexus_requests_session, "post")
@pytest.mark.parametrize("use_hoster", [True, False])
def test_upload_asset_only_component(mock_post, use_hoster):
    mock_open = mock.mock_open(read_data=b"some tgz file")
    mock_post.return_value.ok = True

    with mock.patch("cachito.workers.nexus.open", mock_open):
        nexus.upload_asset_only_component(
            "cachito-js-hosted", "npm", "/path/to/rxjs-6.5.5.tgz", use_hoster
        )

    expected_asset = {"npm.asset": ("rxjs-6.5.5.tgz", b"some tgz file")}
    assert mock_post.call_args[1]["files"] == expected_asset
    assert mock_post.call_args[1]["params"] == {"repository": "cachito-js-hosted"}
    assert mock_post.call_args[1]["auth"].username == "cachito"
    assert mock_post.call_args[1]["auth"].password == "cachito"


@mock.patch.object(nexus.nexus_requests_session, "post")
def test_upload_asset_only_component_connection_error(mock_post):
    mock_open = mock.mock_open(read_data=b"some tgz file")
    mock_post.side_effect = requests.ConnectionError()

    expected = "Could not connect to the Nexus instance to upload a component"
    with mock.patch("cachito.workers.nexus.open", mock_open):
        with pytest.raises(UploadError, match=expected):
            nexus.upload_asset_only_component("cachito-js-hosted", "npm", "/path/to/rxjs-6.5.5.tgz")


@mock.patch.object(nexus.nexus_requests_session, "post")
def test_upload_asset_only_component_failed(mock_post):
    mock_open = mock.mock_open(read_data=b"some tgz file")
    mock_post.return_value.ok = False

    expected = "Failed to upload a component to Nexus"
    with mock.patch("cachito.workers.nexus.open", mock_open):
        with pytest.raises(UploadError, match=expected):
            nexus.upload_asset_only_component("cachito-js-hosted", "npm", "/path/to/rxjs-6.5.5.tgz")


def test_upload_asset_only_component_wrong_type():
    repo_type = "unsupported"
    expected = f"Type {repo_type!r} is not supported or requires additional params"
    with pytest.raises(ValueError, match=expected):
        nexus.upload_asset_only_component("cachito-js-hosted", repo_type, "/path/to/rxjs-6.5.5.tgz")


@mock.patch.object(nexus.nexus_requests_session, "post")
@pytest.mark.parametrize("use_hoster", [True, False])
def test_upload_raw_component(mock_post, use_hoster):
    mock_open = mock.mock_open(read_data=b"some tgz file")
    mock_post.return_value.ok = True

    components = [{"path": "path/to/foo-1.0.0.tgz", "filename": "foo-1.0.0.tar.gz"}]
    with mock.patch("cachito.workers.nexus.open", mock_open):
        nexus.upload_raw_component("cachito-pip-raw", "foo/1.0.0", components, use_hoster)

    assert mock_post.call_args[1]["data"] == {
        "raw.asset1.filename": "foo-1.0.0.tar.gz",
        "raw.directory": "foo/1.0.0",
    }
    assert mock_post.call_args[1]["files"] == {"raw.asset1": b"some tgz file"}
    assert mock_post.call_args[1]["params"] == {"repository": "cachito-pip-raw"}
    assert mock_post.call_args[1]["auth"].username == "cachito"
    assert mock_post.call_args[1]["auth"].password == "cachito"


@mock.patch.object(nexus.nexus_requests_session, "post")
def test_upload_raw_component_failed(mock_post):
    mock_open = mock.mock_open(read_data=b"some tgz file")
    mock_post.return_value.ok = False

    components = [{"path": "path/to/foo-1.0.0.tgz", "filename": "foo-1.0.0.tar.gz"}]
    expected = "Failed to upload a component to Nexus"
    with mock.patch("cachito.workers.nexus.open", mock_open):
        with pytest.raises(UploadError, match=expected):
            nexus.upload_raw_component("cachito-pip-raw", "foo/1.0.0", components)
