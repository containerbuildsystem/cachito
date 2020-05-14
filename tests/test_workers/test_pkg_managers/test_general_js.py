# SPDX-License-Identifier: GPL-3.0-or-later
import json
import io
import os
import tarfile
import textwrap
from unittest import mock

import pytest

from cachito.errors import CachitoError
from cachito.workers.errors import NexusScriptError
from cachito.workers.pkg_managers import general_js
from cachito.workers.paths import RequestBundleDir


@mock.patch("tempfile.TemporaryDirectory")
@mock.patch("cachito.workers.pkg_managers.general_js.generate_and_write_npmrc_file")
@mock.patch("cachito.workers.pkg_managers.general_js.run_cmd")
@mock.patch("shutil.move")
@mock.patch("cachito.workers.paths.get_worker_config")
def test_download_dependencies(mock_gwc, mock_move, mock_run_cmd, mock_gawnf, mock_td, tmpdir):
    bundles_dir = tmpdir.mkdir("bundles")
    mock_gwc.return_value.cachito_bundles_dir = str(bundles_dir)
    mock_td.return_value.__enter__.return_value = "/tmp/cachito-agfdsk"
    mock_run_cmd.return_value = textwrap.dedent(
        """\
        angular-devkit-architect-0.803.26.tgz
        angular-animations-8.2.14.tgz
        rxjs-6.5.5-external-78032157f5c1655436829017bbda787565b48c30.tgz
        """
    )
    deps = [
        {
            "bundled": False,
            "dev": True,
            "name": "@angular-devkit/architect",
            "version": "0.803.26",
            "version_in_nexus": None,
        },
        {
            "bundled": False,
            "dev": False,
            "name": "@angular/animations",
            "version": "8.2.14",
            "version_in_nexus": None,
        },
        {
            "bundled": True,
            "dev": True,
            "name": "object-assign",
            "version": "4.1.1",
            "version_in_nexus": None,
        },
        {
            "bundled": False,
            "dev": False,
            "name": "rxjs",
            "version": "github:ReactiveX/rxjs#78032157f5c1655436829017bbda787565b48c30",
            "version_in_nexus": "6.5.5-external-78032157f5c1655436829017bbda787565b48c30",
        },
    ]
    request_id = 1
    request_bundle_dir = bundles_dir.mkdir("temp").mkdir(str(request_id))
    npm_dir_path = os.path.join(request_bundle_dir, "deps/npm")
    general_js.download_dependencies(request_id, deps)

    mock_gawnf.assert_called_once()
    mock_run_cmd.assert_called_once()
    # This ensures that the bundled dependency is skipped
    expected_npm_pack = [
        "npm",
        "pack",
        "@angular-devkit/architect@0.803.26",
        "@angular/animations@8.2.14",
        "rxjs@6.5.5-external-78032157f5c1655436829017bbda787565b48c30",
    ]
    assert mock_run_cmd.call_args[0][0] == expected_npm_pack
    run_cmd_env_vars = mock_run_cmd.call_args[0][1]["env"]
    assert run_cmd_env_vars["NPM_CONFIG_CACHE"] == "/tmp/cachito-agfdsk/cache"
    assert run_cmd_env_vars["NPM_CONFIG_USERCONFIG"] == "/tmp/cachito-agfdsk/.npmrc"
    assert mock_run_cmd.call_args[0][1]["cwd"] == f"{npm_dir_path}"
    dep1_source_path = RequestBundleDir(f"{npm_dir_path}/angular-devkit-architect-0.803.26.tgz")
    dep1_dest_path = RequestBundleDir(
        f"{npm_dir_path}/@angular-devkit/architect/angular-devkit-architect-0.803.26.tgz"
    )
    dep2_source_path = RequestBundleDir(f"{npm_dir_path}/angular-animations-8.2.14.tgz")
    dep2_dest_path = RequestBundleDir(
        f"{npm_dir_path}/@angular/animations/angular-animations-8.2.14.tgz"
    )
    dep3_source_path = RequestBundleDir(
        f"{npm_dir_path}/rxjs-6.5.5-external-78032157f5c1655436829017bbda787565b48c30.tgz"
    )
    dep3_dest_path = RequestBundleDir(
        f"{npm_dir_path}/rxjs/rxjs-6.5.5-external-78032157f5c1655436829017bbda787565b48c30.tgz"
    )
    mock_move.assert_has_calls(
        [
            mock.call(dep1_source_path, dep1_dest_path),
            mock.call(dep2_source_path, dep2_dest_path),
            mock.call(dep3_source_path, dep3_dest_path),
        ]
    )


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


def test_find_package_json(tmpdir):
    tarfile_path = os.path.join(tmpdir, "npm-package.tgz")
    with tarfile.open(tarfile_path, "x:gz") as archive:
        archive.addfile(tarfile.TarInfo("in/a/galaxy/far/far/away/package.json"))
        archive.addfile(tarfile.TarInfo("in/too/deep/package.json"))
        archive.addfile(tarfile.TarInfo("wrong_file.json"))
        archive.addfile(tarfile.TarInfo("package/index.html"))
        archive.addfile(tarfile.TarInfo("package2/package.json"))
        archive.addfile(tarfile.TarInfo("package/package.json"))

    assert general_js.find_package_json(tarfile_path) == "package2/package.json"


def test_find_package_json_no_package_json(tmpdir):
    tarfile_path = os.path.join(tmpdir, "random.tgz")
    with tarfile.open(tarfile_path, "x:gz") as archive:
        archive.addfile(tarfile.TarInfo("wrong_file.json"), b"{}")
        archive.addfile(
            tarfile.TarInfo("package/tom_hanks_quotes.html"),
            b"<p>Life is like a box of chocolates. You never know what you're gonna get.<p>",
        )
    assert general_js.find_package_json(tarfile_path) is None


@pytest.mark.parametrize("custom_ca_path", (None, "./registry-ca.pem"))
def test_generate_npmrc_content(custom_ca_path):
    npm_rc = general_js.generate_npmrc_content(
        1, "admin", "admin123", custom_ca_path=custom_ca_path
    )

    expected = textwrap.dedent(
        f"""\
        registry=http://nexus:8081/repository/cachito-js-1/
        email=noreply@domain.local
        always-auth=true
        _auth=YWRtaW46YWRtaW4xMjM=
        fetch-retries=5
        fetch-retry-factor=2
        strict-ssl=true
        """
    )

    if custom_ca_path:
        expected += f'cafile="{custom_ca_path}"\n'

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


def test_get_js_hosted_repo_name():
    assert general_js.get_js_hosted_repo_name() == "cachito-js-hosted"


def test_get_js_proxy_repo_name():
    assert general_js.get_js_proxy_repo_name(3) == "cachito-js-3"


def test_get_js_proxy_repo_url():
    assert general_js.get_js_proxy_repo_url(3).endswith("/repository/cachito-js-3/")


def test_get_js_proxy_username():
    assert general_js.get_js_proxy_username(3) == "cachito-js-3"


@pytest.mark.parametrize("group", ("@reactive", None))
@mock.patch("cachito.workers.pkg_managers.general_js.nexus.get_component_info_from_nexus")
def test_get_npm_component_info_from_nexus(mock_gcifn, group):
    if group:
        identifier = f"{group}/rxjs"
    else:
        identifier = "rxjs"

    component = {
        "id": "Y2FjaGl0by1qcy1ob3N0ZWQ6ZDQ4MTE3NTQxZGNiODllYzYxM2IyMzk3MzIwMWQ3YmE",
        "repository": "cachito-js-hosted",
        "format": "npm",
        "group": group[1:] if group else None,
        "name": "rxjs",
        "version": "6.5.5-external-78032157f5c1655436829017bbda787565b48c30",
    }
    mock_gcifn.return_value = component

    rv = general_js.get_npm_component_info_from_nexus(
        identifier, "6.5.5-external-78032157f5c1655436829017bbda787565b48c30", max_attempts=3
    )

    assert rv == component
    if group:
        mock_gcifn.assert_called_once_with(
            "cachito-js-hosted",
            "npm",
            "rxjs",
            "6.5.5-external-78032157f5c1655436829017bbda787565b48c30",
            "reactive",
            3,
        )
    else:
        mock_gcifn.assert_called_once_with(
            "cachito-js-hosted",
            "npm",
            "rxjs",
            "6.5.5-external-78032157f5c1655436829017bbda787565b48c30",
            None,
            3,
        )


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


@mock.patch("cachito.workers.pkg_managers.general_js.tempfile.TemporaryDirectory")
@mock.patch("cachito.workers.pkg_managers.general_js.run_cmd")
@mock.patch("cachito.workers.pkg_managers.general_js.find_package_json")
@mock.patch("cachito.workers.pkg_managers.general_js.nexus.upload_artifact")
def test_upload_non_registry_dependency(mock_ua, mock_fpj, mock_run_cmd, mock_td, tmpdir):
    tarfile_path = os.path.join(tmpdir, "star-wars-5.0.0.tgz")
    with tarfile.open(tarfile_path, "x:gz") as archive:
        tar_info = tarfile.TarInfo("package/salutation.html")
        content = b"<h1>Bonjour monsieur Solo!</h1>"
        tar_info.size = len(content)
        archive.addfile(tar_info, io.BytesIO(content))

        tar_info = tarfile.TarInfo("package/package.json")
        content = b'{"version": "5.0.0"}'
        tar_info.size = len(content)
        archive.addfile(tar_info, io.BytesIO(content))

    mock_td.return_value.__enter__.return_value = str(tmpdir)
    mock_run_cmd.return_value = "star-wars-5.0.0.tgz\n"
    mock_fpj.return_value = "package/package.json"

    general_js.upload_non_registry_dependency("star-wars@5.0.0", "-the-empire-strikes-back")

    modified_tarfile_path = os.path.join(tmpdir, "modified-star-wars-5.0.0.tgz")
    with tarfile.open(modified_tarfile_path, "r:*") as f:
        # Verify that the archive has the original members in order
        members = {m.path for m in f.getmembers()}
        assert members == {"package/salutation.html", "package/package.json"}
        # Verify that the archive had its version updated
        new_version = json.load(f.extractfile("package/package.json"))["version"]
        assert new_version == "5.0.0-the-empire-strikes-back"

    mock_run_cmd.assert_called_once_with(["npm", "pack", "star-wars@5.0.0"], mock.ANY, mock.ANY)
    mock_fpj.assert_called_once_with(tarfile_path)
    mock_ua.assert_called_once_with("cachito-js-hosted", "npm", modified_tarfile_path)


@mock.patch("cachito.workers.pkg_managers.general_js.tempfile.TemporaryDirectory")
@mock.patch("cachito.workers.pkg_managers.general_js.run_cmd")
@mock.patch("cachito.workers.pkg_managers.general_js.find_package_json")
def test_upload_non_registry_dependency_no_package_json(mock_fpj, mock_run_cmd, mock_td, tmpdir):
    mock_td.return_value.__enter__.return_value = str(tmpdir)
    mock_run_cmd.return_value = "star-wars-5.0.0.tgz\n"
    mock_fpj.return_value = None

    expected = "The dependency star-wars@5.0.0 does not have a package.json file"
    with pytest.raises(CachitoError, match=expected):
        general_js.upload_non_registry_dependency("star-wars@5.0.0", "-the-empire-strikes-back")


@mock.patch("cachito.workers.pkg_managers.general_js.tempfile.TemporaryDirectory")
@mock.patch("cachito.workers.pkg_managers.general_js.run_cmd")
@mock.patch("cachito.workers.pkg_managers.general_js.find_package_json")
@mock.patch("cachito.workers.pkg_managers.general_js.nexus.upload_artifact")
def test_upload_non_registry_dependency_invalid_package_json(
    mock_ua, mock_fpj, mock_run_cmd, mock_td, tmpdir
):
    tarfile_path = os.path.join(tmpdir, "star-wars-5.0.0.tgz")
    with tarfile.open(tarfile_path, "x:gz") as archive:
        tar_info = tarfile.TarInfo("package/package.json")
        content = b"Not JSON!"
        tar_info.size = len(content)
        archive.addfile(tar_info, io.BytesIO(content))

    mock_td.return_value.__enter__.return_value = str(tmpdir)
    mock_run_cmd.return_value = "star-wars-5.0.0.tgz\n"
    mock_fpj.return_value = "package/package.json"

    expected = "The dependency star-wars@5.0.0 does not have a valid package.json file"
    with pytest.raises(CachitoError, match=expected):
        general_js.upload_non_registry_dependency("star-wars@5.0.0", "-the-empire-strikes-back")
