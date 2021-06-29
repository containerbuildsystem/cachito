# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
from unittest import mock

import pytest

from cachito.common.paths import RequestBundleDir as BaseRequestBundleDir
from cachito.errors import CachitoError
from cachito.workers.paths import RequestBundleDir
from cachito.workers.tasks import npm


def test_verify_npm_files(tmpdir):
    app_dir = tmpdir.mkdir("temp").mkdir("1").mkdir("app")
    app_dir.join("package.json").write(b"{}")
    app_dir.join("package-lock.json").write(b"{}")
    bundle_dir = BaseRequestBundleDir(1, str(tmpdir))

    npm._verify_npm_files(bundle_dir, ["."])


def test_verify_npm_files_no_lock_file(tmpdir):
    app_dir = tmpdir.mkdir("temp").mkdir("1").mkdir("app").mkdir("client")
    app_dir.join("package.json").write(b"{}")
    bundle_dir = BaseRequestBundleDir(1, str(tmpdir))

    expected = (
        "The client/npm-shrinkwrap.json or client/package-lock.json file must be present for the "
        "npm package manager"
    )
    with pytest.raises(CachitoError, match=expected):
        npm._verify_npm_files(bundle_dir, ["client"])


def test_verify_npm_files_no_package_json(tmpdir):
    app_dir = tmpdir.mkdir("temp").mkdir("1").mkdir("app").mkdir("client")
    app_dir.join("package-lock.json").write(b"{}")
    bundle_dir = BaseRequestBundleDir(1, str(tmpdir))

    expected = "The client/package.json file must be present for the npm package manager"
    with pytest.raises(CachitoError, match=expected):
        npm._verify_npm_files(bundle_dir, ["client"])


def test_verify_npm_files_node_modules(tmpdir):
    app_dir = tmpdir.mkdir("temp").mkdir("1").mkdir("app").mkdir("client")
    app_dir.join("package.json").write(b"{}")
    app_dir.join("package-lock.json").write(b"{}")
    app_dir.mkdir("node_modules")
    bundle_dir = BaseRequestBundleDir(1, str(tmpdir))

    expected = "The client/node_modules directory cannot be present in the source repository"
    with pytest.raises(CachitoError, match=expected):
        npm._verify_npm_files(bundle_dir, ["client"])


@mock.patch("cachito.workers.tasks.npm.nexus.execute_script")
def test_cleanup_npm_request(mock_exec_script):
    npm.cleanup_npm_request(3)

    expected_payload = {"repository_name": "cachito-npm-3", "username": "cachito-npm-3"}
    mock_exec_script.assert_called_once_with("js_cleanup", expected_payload)


# The package.json and package-lock.json mock values are not actually valid,
# they just need to be valid JSON
@pytest.mark.parametrize("package_json", (None, {"name": "han-solo"}))
@pytest.mark.parametrize("lock_file", (None, {"dependencies": []}))
@pytest.mark.parametrize("ca_file", (None, "some CA file contents"))
@pytest.mark.parametrize(
    "package_subpath, subpath_as_path_component, reverse_path_component",
    [(None, "", ""), (".", "", ""), ("some/path", "some/path/", "../../")],
)
@mock.patch("cachito.workers.paths.get_worker_config")
@mock.patch("cachito.workers.tasks.npm._verify_npm_files")
@mock.patch("cachito.workers.tasks.npm.set_request_state")
@mock.patch("cachito.workers.tasks.npm.get_request")
@mock.patch("cachito.workers.tasks.npm.prepare_nexus_for_js_request")
@mock.patch("cachito.workers.tasks.npm.resolve_npm")
@mock.patch("cachito.workers.tasks.npm.finalize_nexus_for_js_request")
@mock.patch("cachito.workers.tasks.npm.nexus.get_ca_cert")
@mock.patch("cachito.workers.tasks.npm.generate_npmrc_content")
@mock.patch("cachito.workers.tasks.npm.update_request_with_config_files")
@mock.patch("cachito.workers.tasks.npm.update_request_env_vars")
def test_fetch_npm_source(
    mock_update_request_env_vars,
    mock_urwcf,
    mock_gnc,
    mock_gcc,
    mock_fnfjr,
    mock_rn,
    mock_pnfjr,
    mock_get_request,
    mock_srs,
    mock_vnf,
    get_worker_config,
    ca_file,
    lock_file,
    package_json,
    package_subpath,
    subpath_as_path_component,
    reverse_path_component,
    task_passes_state_check,
    tmpdir,
):
    get_worker_config.return_value = mock.Mock(cachito_bundles_dir=tmpdir)
    request_id = 6
    request = {"id": request_id}
    mock_get_request.return_value = request
    package = {"name": "han-solo", "type": "npm", "version": "5.0.0"}
    deps = [
        {"dev": False, "name": "@angular/animations", "type": "npm", "version": "8.2.14"},
        {"dev": False, "name": "tslib", "type": "npm", "version": "1.11.1"},
    ]
    mock_rn.return_value = {
        "deps": deps,
        "downloaded_deps": {"@angular/animations@8.2.14", "tslib@1.11.1"},
        "lock_file": lock_file,
        "lock_file_name": "package-lock.json",
        "package": package,
        "package.json": package_json,
    }
    username = f"cachito-npm-{request_id}"
    password = "asjfhjsdfkwe"
    mock_fnfjr.return_value = password
    mock_gcc.return_value = ca_file
    mock_gnc.return_value = "some npmrc"

    if package_subpath:
        package_configs = [{"path": package_subpath}]
    else:
        package_configs = None

    npm.fetch_npm_source(request_id, package_configs=package_configs)

    bundle_dir = RequestBundleDir(request_id)
    mock_vnf.assert_called_once_with(bundle_dir, [package_subpath or "."])
    assert mock_srs.call_count == 3
    assert mock_get_request.called_once_with(request_id)
    mock_pnfjr.assert_called_once_with("cachito-npm-6")
    lock_file_path = str(bundle_dir.app_subpath(package_subpath or ".").source_dir)
    mock_rn.assert_called_once_with(lock_file_path, request, skip_deps=set())
    if ca_file:
        mock_gnc.assert_called_once_with(
            "http://nexus:8081/repository/cachito-npm-6/",
            username,
            password,
            custom_ca_path=f"{reverse_path_component}registry-ca.pem",
        )
    else:
        mock_gnc.assert_called_once_with(
            "http://nexus:8081/repository/cachito-npm-6/", username, password, custom_ca_path=None
        )

    expected_config_files = []
    if package_json:
        expected_config_files.append(
            {
                "content": "ewogICJuYW1lIjogImhhbi1zb2xvIgp9",
                "path": f"app/{subpath_as_path_component}package.json",
                "type": "base64",
            }
        )

    if lock_file:
        expected_config_files.append(
            {
                "content": "ewogICJkZXBlbmRlbmNpZXMiOiBbXQp9",
                "path": f"app/{subpath_as_path_component}package-lock.json",
                "type": "base64",
            }
        )

    if ca_file:
        expected_config_files.append(
            {
                "content": "c29tZSBDQSBmaWxlIGNvbnRlbnRz",
                "path": "app/registry-ca.pem",
                "type": "base64",
            }
        )

    expected_config_files.append(
        {
            "content": "c29tZSBucG1yYw==",
            "path": f"app/{subpath_as_path_component}.npmrc",
            "type": "base64",
        }
    )
    mock_urwcf.assert_called_once_with(request_id, expected_config_files)

    mock_update_request_env_vars.assert_called_once_with(
        request_id,
        {
            "CHROMEDRIVER_SKIP_DOWNLOAD": {"value": "true", "kind": "literal"},
            "SKIP_SASS_BINARY_DOWNLOAD_FOR_CI": {"value": "true", "kind": "literal"},
        },
    )

    pkg_info = package.copy()
    pkg_info["dependencies"] = deps
    if package_subpath and package_subpath != os.curdir:
        pkg_info["path"] = package_subpath
    assert {"packages": [pkg_info]} == json.loads(bundle_dir.npm_packages_data.read_bytes())


@mock.patch("cachito.workers.tasks.npm.RequestBundleDir")
@mock.patch("cachito.workers.tasks.npm._verify_npm_files")
@mock.patch("cachito.workers.tasks.npm.set_request_state")
@mock.patch("cachito.workers.tasks.npm.get_request")
@mock.patch("cachito.workers.tasks.npm.prepare_nexus_for_js_request")
@mock.patch("cachito.workers.tasks.npm.resolve_npm")
@mock.patch("cachito.workers.tasks.npm.finalize_nexus_for_js_request")
@mock.patch("cachito.workers.tasks.npm.nexus.get_ca_cert")
@mock.patch("cachito.workers.tasks.npm.generate_npmrc_content")
@mock.patch("cachito.workers.tasks.npm.update_request_with_config_files")
@mock.patch("cachito.workers.tasks.npm.update_request_env_vars")
def test_fetch_npm_source_multiple_paths(
    mock_update_request_env_vars,
    mock_urwcf,
    mock_gnc,
    mock_gcc,
    mock_fnfjr,
    mock_rn,
    mock_pnfjr,
    mock_get_request,
    mock_srs,
    mock_vnf,
    mock_rbd,
    task_passes_state_check,
):
    request_id = 6
    request = {"id": request_id}
    mock_get_request.return_value = request
    package = {"name": "han-solo", "type": "npm", "version": "5.0.0"}
    package_two = {"name": "han-solo", "type": "npm", "version": "6.0.0"}
    deps = [
        {"dev": False, "name": "@angular/animations", "type": "npm", "version": "8.2.14"},
        {"dev": False, "name": "tslib", "type": "npm", "version": "1.11.1"},
    ]
    # The package.json and package-lock.json mock values are not actually valid,
    # they just need to be valid JSON
    mock_rn.side_effect = [
        {
            "deps": deps,
            "downloaded_deps": {"@angular/animations@8.2.14", "tslib@1.11.1"},
            "lock_file": {"dependencies": []},
            "lock_file_name": "package-lock.json",
            "package": package,
            "package.json": {"name": "han-solo", "version": "5.0.0"},
        },
        {
            "deps": deps,
            "downloaded_deps": {"@angular/animations@8.2.14", "tslib@1.11.1"},
            "lock_file": {"dependencies": []},
            "lock_file_name": "package-lock.json",
            "package": package_two,
            "package.json": {"name": "han-solo", "version": "6.0.0"},
        },
    ]
    ca_file = "some CA file contents"
    mock_gcc.return_value = ca_file
    mock_gnc.return_value = "some npmrc"

    npm.fetch_npm_source(request_id, [{"path": "old-client"}, {"path": "new-client/client"}])

    mock_vnf.assert_called_once_with(mock_rbd.return_value, ["old-client", "new-client/client"])
    mock_pnfjr.assert_called_once()
    mock_rn.assert_has_calls(
        (
            mock.call(
                str(mock_rbd().app_subpath("old-client").source_dir), request, skip_deps=set()
            ),
            mock.call(
                str(mock_rbd().app_subpath("new-client/client").source_dir),
                request,
                skip_deps={"@angular/animations@8.2.14", "tslib@1.11.1"},
            ),
        )
    )
    mock_gnc.assert_has_calls(
        (
            mock.call(mock.ANY, mock.ANY, mock.ANY, custom_ca_path="../registry-ca.pem"),
            mock.call(mock.ANY, mock.ANY, mock.ANY, custom_ca_path="../../registry-ca.pem"),
        )
    )

    expected_config_files = [
        {
            "content": "ewogICJuYW1lIjogImhhbi1zb2xvIiwKICAidmVyc2lvbiI6ICI1LjAuMCIKfQ==",
            "path": "app/old-client/package.json",
            "type": "base64",
        },
        {
            "content": "ewogICJkZXBlbmRlbmNpZXMiOiBbXQp9",
            "path": "app/old-client/package-lock.json",
            "type": "base64",
        },
        {
            "content": "ewogICJuYW1lIjogImhhbi1zb2xvIiwKICAidmVyc2lvbiI6ICI2LjAuMCIKfQ==",
            "path": "app/new-client/client/package.json",
            "type": "base64",
        },
        {
            "content": "ewogICJkZXBlbmRlbmNpZXMiOiBbXQp9",
            "path": "app/new-client/client/package-lock.json",
            "type": "base64",
        },
        {
            "content": "c29tZSBDQSBmaWxlIGNvbnRlbnRz",
            "path": "app/registry-ca.pem",
            "type": "base64",
        },
        {"content": "c29tZSBucG1yYw==", "path": "app/old-client/.npmrc", "type": "base64"},
        {"content": "c29tZSBucG1yYw==", "path": "app/new-client/client/.npmrc", "type": "base64"},
    ]

    mock_urwcf.assert_called_once_with(request_id, expected_config_files)

    mock_update_request_env_vars.assert_called_once_with(
        request_id,
        {
            "CHROMEDRIVER_SKIP_DOWNLOAD": {"kind": "literal", "value": "true"},
            "SKIP_SASS_BINARY_DOWNLOAD_FOR_CI": {"kind": "literal", "value": "true"},
        },
    )


@mock.patch("cachito.workers.tasks.npm.RequestBundleDir")
@mock.patch("cachito.workers.tasks.npm._verify_npm_files")
@mock.patch("cachito.workers.tasks.npm.set_request_state")
@mock.patch("cachito.workers.tasks.npm.get_request")
@mock.patch("cachito.workers.tasks.npm.prepare_nexus_for_js_request")
@mock.patch("cachito.workers.tasks.npm.resolve_npm")
def test_fetch_npm_source_resolve_fails(
    mock_rn, mock_pnfjr, mock_get_request, mock_srs, mock_vnf, mock_rbd, task_passes_state_check
):
    request_id = 6
    request = {"id": request_id}
    mock_get_request.return_value = request
    mock_rn.side_effect = CachitoError("Some error")

    with pytest.raises(CachitoError, match="Some error"):
        npm.fetch_npm_source(request_id)

    assert mock_srs.call_count == 2
    mock_get_request.assert_called_once_with(request_id)
    mock_pnfjr.assert_called_once_with("cachito-npm-6")


@mock.patch("cachito.workers.tasks.npm.nexus.get_ca_cert")
@mock.patch("cachito.workers.tasks.npm.make_base64_config_file")
@mock.patch("cachito.workers.tasks.npm.generate_npmrc_content")
@pytest.mark.parametrize("has_ca_cert", [True, False])
def test_generate_npmrc_config_files(
    mock_generate_content, mock_make_config_file, mock_get_cert, has_ca_cert
):
    url = "http://example.org"
    username = "nicola"
    password = "tesla"
    subpaths = [".", "foo", "foo/bar"]

    npmrc_contents = [mock.Mock(), mock.Mock(), mock.Mock()]
    mock_generate_content.side_effect = npmrc_contents

    expected_configs = [mock.Mock(), mock.Mock(), mock.Mock()]
    expected_make_cfg_calls = [
        mock.call(npmrc_contents[0], "app/.npmrc"),
        mock.call(npmrc_contents[1], "app/foo/.npmrc"),
        mock.call(npmrc_contents[2], "app/foo/bar/.npmrc"),
    ]

    if has_ca_cert:
        mock_get_cert.return_value = "some CA cert"
        expected_ca_pem = mock.Mock()
        expected_configs.insert(0, expected_ca_pem)
        expected_make_cfg_calls.insert(
            0, mock.call(mock_get_cert.return_value, "app/registry-ca.pem")
        )
        expected_content_calls = [
            mock.call(url, username, password, custom_ca_path="registry-ca.pem"),
            mock.call(url, username, password, custom_ca_path="../registry-ca.pem"),
            mock.call(url, username, password, custom_ca_path="../../registry-ca.pem"),
        ]
    else:
        mock_get_cert.return_value = None
        expected_content_calls = [
            mock.call(url, username, password, custom_ca_path=None),
            mock.call(url, username, password, custom_ca_path=None),
            mock.call(url, username, password, custom_ca_path=None),
        ]

    mock_make_config_file.side_effect = expected_configs

    rv = npm.generate_npmrc_config_files(url, username, password, subpaths)
    assert rv == expected_configs

    mock_get_cert.assert_called_once()
    mock_generate_content.assert_has_calls(expected_content_calls)
    mock_make_config_file.assert_has_calls(expected_make_cfg_calls)
