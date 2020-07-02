# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

import pytest

from cachito.errors import CachitoError
from cachito.workers.tasks import npm


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
@mock.patch("cachito.workers.tasks.npm.RequestBundleDir")
@mock.patch("cachito.workers.tasks.npm.set_request_state")
@mock.patch("cachito.workers.tasks.npm.prepare_nexus_for_js_request")
@mock.patch("cachito.workers.tasks.npm.resolve_npm")
@mock.patch("cachito.workers.tasks.npm.finalize_nexus_for_js_request")
@mock.patch("cachito.workers.tasks.npm.nexus.get_ca_cert")
@mock.patch("cachito.workers.tasks.npm.generate_npmrc_content")
@mock.patch("cachito.workers.tasks.npm.update_request_with_config_files")
@mock.patch("cachito.workers.tasks.npm.update_request_with_package")
@mock.patch("cachito.workers.tasks.npm.update_request_with_deps")
def test_fetch_npm_source(
    mock_urwd,
    mock_urwp,
    mock_urwcf,
    mock_gnc,
    mock_gcc,
    mock_fnfjr,
    mock_rn,
    mock_pnfjr,
    mock_srs,
    mock_rbd,
    ca_file,
    lock_file,
    package_json,
):
    mock_rbd.return_value.npm_shrinkwrap_file.exists.return_value = False
    mock_rbd.return_value.npm_package_lock_file.exists.return_value = True
    # The mock package-lock.json Path object must act like one for os.path functions
    mock_rbd.return_value.npm_package_lock_file.__str__.return_value = "/path/to/package-lock.json"
    mock_rbd.return_value.npm_package_file.exists.return_value = True
    # The node_modules directory doesn't exist
    mock_rbd.return_value.npm_deps_dir.joinpath.return_value.exists.return_value = False
    request_id = 6
    request = {"id": request_id}
    mock_srs.return_value = request
    package = {"name": "han-solo", "type": "npm", "version": "5.0.0"}
    deps = [
        {"dev": False, "name": "@angular/animations", "type": "npm", "version": "8.2.14"},
        {"dev": False, "name": "tslib", "type": "npm", "version": "1.11.1"},
    ]
    mock_rn.return_value = {
        "deps": deps,
        "lock_file": lock_file,
        "package": package,
        "package.json": package_json,
    }
    username = f"cachito-npm-{request_id}"
    password = "asjfhjsdfkwe"
    mock_fnfjr.return_value = password
    mock_gcc.return_value = ca_file
    mock_gnc.return_value = "some npmrc"

    npm.fetch_npm_source(request_id)

    assert mock_srs.call_count == 3
    mock_pnfjr.assert_called_once_with("cachito-npm-6")
    lock_file_path = str(mock_rbd().source_dir)
    mock_rn.assert_called_once_with(lock_file_path, request)
    if ca_file:
        mock_gnc.assert_called_once_with(
            "http://nexus:8081/repository/cachito-npm-6/",
            username,
            password,
            custom_ca_path="./registry-ca.pem",
        )
    else:
        mock_gnc.assert_called_once_with(
            "http://nexus:8081/repository/cachito-npm-6/", username, password, custom_ca_path=None
        )

    expected_config_files = []
    if ca_file:
        expected_config_files.append(
            {
                "content": "c29tZSBDQSBmaWxlIGNvbnRlbnRz",
                "path": "app/registry-ca.pem",
                "type": "base64",
            }
        )

    expected_config_files.append(
        {"content": "c29tZSBucG1yYw==", "path": "app/.npmrc", "type": "base64"}
    )

    if package_json:
        expected_config_files.append(
            {
                "content": "ewogICJuYW1lIjogImhhbi1zb2xvIgp9",
                "path": "app/package.json",
                "type": "base64",
            }
        )

    if lock_file:
        expected_config_files.append(
            {
                "content": "ewogICJkZXBlbmRlbmNpZXMiOiBbXQp9",
                "path": "app/package-lock.json",
                "type": "base64",
            }
        )

    mock_urwcf.assert_called_once_with(request_id, expected_config_files)
    mock_urwp.assert_called_once_with(
        request_id,
        package,
        {
            "CHROMEDRIVER_SKIP_DOWNLOAD": {"value": "true", "kind": "literal"},
            "SKIP_SASS_BINARY_DOWNLOAD_FOR_CI": {"value": "true", "kind": "literal"},
        },
    )
    mock_urwd.assert_called_once_with(request_id, package, deps)


@mock.patch("cachito.workers.tasks.npm.RequestBundleDir")
def test_fetch_npm_source_no_lock(mock_rbd):
    mock_rbd.return_value.npm_shrinkwrap_file.exists.return_value = False
    mock_rbd.return_value.npm_package_lock_file.exists.return_value = False

    expected = (
        "The npm-shrinkwrap.json or package-lock.json file must be present for the npm package "
        "manager"
    )
    with pytest.raises(CachitoError, match=expected):
        npm.fetch_npm_source(6)


@mock.patch("cachito.workers.tasks.npm.RequestBundleDir")
def test_fetch_npm_source_no_package_json(mock_rbd):
    mock_rbd.return_value.npm_shrinkwrap_file.exists.return_value = True
    mock_rbd.return_value.npm_package_lock_file.exists.return_value = True
    # The node_modules directory doesn't exist
    mock_rbd.return_value.npm_deps_dir.joinpath.return_value.exists.return_value = False
    mock_rbd.return_value.npm_package_file.exists.return_value = False

    expected = "The package.json file is not present in the source repository"
    with pytest.raises(CachitoError, match=expected):
        npm.fetch_npm_source(6)


@mock.patch("cachito.workers.tasks.npm.RequestBundleDir")
def test_fetch_npm_source_node_modules_exists(mock_rbd):
    mock_rbd.return_value.npm_shrinkwrap_file.exists.return_value = False
    mock_rbd.return_value.npm_package_lock_file.exists.return_value = True
    mock_rbd.return_value.npm_deps_dir.joinpath.return_value.exists.return_value = True

    expected = "The node_modules directory cannot be present in the source repository"
    with pytest.raises(CachitoError, match=expected):
        npm.fetch_npm_source(6)


@mock.patch("cachito.workers.tasks.npm.RequestBundleDir")
@mock.patch("cachito.workers.tasks.npm.set_request_state")
@mock.patch("cachito.workers.tasks.npm.prepare_nexus_for_js_request")
@mock.patch("cachito.workers.tasks.npm.resolve_npm")
def test_fetch_npm_source_resolve_fails(mock_rn, mock_pnfjr, mock_srs, mock_rbd):
    mock_rbd.return_value.npm_shrinkwrap_file.exists.return_value = False
    mock_rbd.return_value.npm_package_lock_file.exists.return_value = True
    mock_rbd.return_value.npm_deps_dir.joinpath.return_value.exists.return_value = False
    request_id = 6
    request = {"id": request_id}
    mock_srs.return_value = request
    mock_rn.side_effect = CachitoError("Some error")

    with pytest.raises(CachitoError, match="Some error"):
        npm.fetch_npm_source(request_id)

    assert mock_srs.call_count == 2
    mock_pnfjr.assert_called_once_with("cachito-npm-6")
