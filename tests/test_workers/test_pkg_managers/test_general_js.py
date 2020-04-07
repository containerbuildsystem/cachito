# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

import pytest
import textwrap

from cachito.errors import CachitoError
from cachito.workers.errors import NexusScriptError
from cachito.workers.pkg_managers import general_js


@mock.patch("tempfile.TemporaryDirectory")
@mock.patch("cachito.workers.pkg_managers.general_js.generate_and_write_npmrc_file")
@mock.patch("cachito.workers.pkg_managers.general_js.RequestBundleDir")
@mock.patch("cachito.workers.pkg_managers.general_js.run_cmd")
def test_download_dependencies(mock_run_cmd, mock_rbd, mock_gawnf, mock_td):
    mock_td.return_value.__enter__.return_value = "/tmp/cachito-agfdsk"

    deps = [
        {"bundled": False, "dev": True, "name": "@angular-devkit/architect", "version": "0.803.26"},
        {"bundled": False, "dev": False, "name": "@angular/animations", "version": "8.2.14"},
        {"bundled": True, "dev": True, "name": "object-assign", "version": "4.1.1"},
    ]
    request_id = 1
    general_js.download_dependencies(request_id, deps)

    mock_gawnf.assert_called_once()
    mock_run_cmd.assert_called_once()
    # This ensures that the bundled dependency is skipped
    expected_npm_pack = [
        "npm",
        "pack",
        "@angular-devkit/architect@0.803.26",
        "@angular/animations@8.2.14",
    ]
    assert mock_run_cmd.call_args[0][0] == expected_npm_pack
    run_cmd_env_vars = mock_run_cmd.call_args[0][1]["env"]
    assert run_cmd_env_vars["NPM_CONFIG_CACHE"] == "/tmp/cachito-agfdsk/cache"
    assert run_cmd_env_vars["NPM_CONFIG_USERCONFIG"] == "/tmp/cachito-agfdsk/.npmrc"
    assert mock_run_cmd.call_args[0][1]["cwd"] == str(mock_rbd().npm_deps_dir)


@mock.patch("cachito.workers.pkg_managers.general_js.nexus.execute_script")
def test_finalize_nexus_for_js_request(mock_exec_script):
    _, password = general_js.finalize_nexus_for_js_request(1)

    mock_exec_script.assert_called_once()
    assert mock_exec_script.call_args[0][0] == "js_after_content_staged"
    payload = mock_exec_script.call_args[0][1]
    assert len(payload["password"]) >= 24
    assert payload.keys() == {"password", "repository_name", "username"}
    assert payload["repository_name"] == "cachito-js-1"
    assert payload["username"] == "cachito-js-1"


@mock.patch("cachito.workers.pkg_managers.general_js.nexus.execute_script")
def test_finalize_nexus_for_js_request_failed(mock_exec_script):
    mock_exec_script.side_effect = NexusScriptError()

    expected = (
        "Failed to configure Nexus to allow the request's npm repository to be ready for "
        "consumption"
    )
    with pytest.raises(CachitoError, match=expected):
        _, password = general_js.finalize_nexus_for_js_request(1)


@pytest.mark.parametrize("ca_exists", (False, True))
@mock.patch("cachito.workers.pkg_managers.general_js.nexus.get_ca_cert")
def test_generate_npmrc_content(mock_get_ca, ca_exists):
    if ca_exists:
        mock_get_ca.return_value = "some cert"
    else:
        mock_get_ca.return_value = None

    npm_rc = general_js.generate_npmrc_content(1, "admin", "admin123")

    expected = textwrap.dedent(
        f"""\
        registry=http://nexus:8081/repository/cachito-js-1/
        email=noreply@domain.local
        always-auth=true
        _auth=YWRtaW46YWRtaW4xMjM=
        fetch-retries=5
        fetch-retry-factor=2
        """
    )

    if ca_exists:
        expected = textwrap.dedent(
            f"""\
            {expected}
            ca="some cert"
            strict-ssl=true
            """
        )

    assert npm_rc == expected


@mock.patch("cachito.workers.pkg_managers.general_js.generate_npmrc_content")
def test_generate_and_write_npmrc_file(mock_gen_npmrc):
    npmrc_content = "registry=http://nexus:8081/repository/cachito-js-1/"
    mock_gen_npmrc.return_value = npmrc_content
    mock_open = mock.mock_open()

    npm_rc_path = "/tmp/cachito-hgfsd/.npmrc"
    with mock.patch("cachito.workers.pkg_managers.general_js.open", mock_open):
        general_js.generate_and_write_npmrc_file(npm_rc_path, 1, "admin", "admin123")

    mock_open.assert_called_once_with(npm_rc_path, "w")
    mock_open().write.assert_called_once_with(npmrc_content)


def test_get_js_proxy_repo_name():
    assert general_js.get_js_proxy_repo_name(3) == "cachito-js-3"


def test_get_js_proxy_repo_url():
    assert general_js.get_js_proxy_repo_url(3).endswith("/repository/cachito-js-3/")


def test_get_js_proxy_username():
    assert general_js.get_js_proxy_username(3) == "cachito-js-3"


@mock.patch("cachito.workers.pkg_managers.general_js.nexus.execute_script")
def test_prepare_nexus_for_js_request(mock_exec_script):
    general_js.prepare_nexus_for_js_request(1)

    mock_exec_script.assert_called_once()
    assert mock_exec_script.call_args[0][0] == "js_before_content_staged"
    payload = mock_exec_script.call_args[0][1]
    assert payload == {
        "repository_name": "cachito-js-1",
        "http_password": "cachito_unprivileged",
        "http_username": "cachito_unprivileged",
    }


@mock.patch("cachito.workers.pkg_managers.general_js.nexus.execute_script")
def test_prepare_nexus_for_js_request_failed(mock_exec_script):
    mock_exec_script.side_effect = NexusScriptError()

    expected = "Failed to prepare Nexus for Cachito to stage JavaScript content"
    with pytest.raises(CachitoError, match=expected):
        _, password = general_js.prepare_nexus_for_js_request(1)
