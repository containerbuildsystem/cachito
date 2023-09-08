# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import os
import os.path
import tarfile
import textwrap
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from cachito.errors import (
    FileAccessError,
    InvalidFileFormat,
    InvalidRepoStructure,
    NexusError,
    UnsupportedFeature,
)
from cachito.workers import nexus
from cachito.workers.config import get_worker_config
from cachito.workers.errors import NexusScriptError
from cachito.workers.pkg_managers import general, general_js, npm


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://registry.yarnpkg.com/chai/-/chai-4.2.0.tgz", True),
        ("https://example.org/fecha.tar.gz", False),
        ("https://registry.npmjs.org/chai/-/chai-4.2.0.tgz", True),
    ],
)
def test_is_from_npm_registry(url, expected):
    assert general_js.is_from_npm_registry(url) == expected


@pytest.mark.parametrize("nexus_ca_cert_exists", (True, False))
@pytest.mark.parametrize("pkg_manager", ["npm", "yarn"])
@mock.patch("tempfile.TemporaryDirectory")
@mock.patch("os.path.exists")
@mock.patch("cachito.workers.pkg_managers.general_js.async_download_binary_file")
@mock.patch("shutil.move")
@mock.patch("cachito.workers.paths.get_worker_config")
def test_download_dependencies(
    mock_gwc,
    mock_move,
    mock_async_download_binary_file,
    mock_exists,
    mock_td,
    nexus_ca_cert_exists,
    pkg_manager,
    tmpdir,
):
    mock_gwc.return_value.cachito_nexus_ca_cert = "/etc/cachito/nexus_ca.pem"
    mock_td_path = tmpdir.mkdir("cachito-agfdsk")
    mock_td.return_value.__enter__.return_value = str(mock_td_path)
    mock_exists.return_value = nexus_ca_cert_exists
    mock_async_download_binary_file.side_effect = lambda s, u, d, tarball_name, auth: tarball_name

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
            "version_in_nexus": "6.5.5-external-gitcommit-78032157f5c1655436829017bbda787565b48c30",
        },
        {
            "bundled": False,
            "dev": False,
            "name": "jsplumb",
            "version": "file:../jsplumb-2.10.2.tgz",
            "version_in_nexus": None,
        },
        {
            "bundled": False,
            "dev": False,
            "name": "exsp",
            "version": "https://github.com/exsp/exsp/archive/2.10.2.tar.gz",
            "version_in_nexus": "2.10.2-external-sha512-abcdefg",
        },
    ]
    request_id = 1
    proxy_repo_url = npm.get_npm_proxy_repo_url(request_id)
    download_dir = tmpdir.join("deps")
    download_dir.mkdir()

    general_js.download_dependencies(
        Path(download_dir), deps, proxy_repo_url, pkg_manager=pkg_manager
    )

    dep1_source_path = os.path.join(download_dir, "angular-devkit-architect-0.803.26.tgz")
    dep1_dest_path = os.path.join(
        download_dir,
        "@angular-devkit/architect/angular-devkit-architect-0.803.26.tgz",
    )
    dep2_source_path = os.path.join(download_dir, "angular-animations-8.2.14.tgz")
    dep2_dest_path = os.path.join(download_dir, "@angular/animations/angular-animations-8.2.14.tgz")
    dep3_source_path = os.path.join(
        download_dir, "rxjs-6.5.5-external-gitcommit-78032157f5c1655436829017bbda787565b48c30.tgz"
    )
    dep3_dest_path = os.path.join(
        download_dir,
        "github/ReactiveX/rxjs/"
        "rxjs-6.5.5-external-gitcommit-78032157f5c1655436829017bbda787565b48c30.tgz",
    )
    dep4_source_path = os.path.join(
        download_dir,
        "exsp-2.10.2-external-sha512-abcdefg.tgz",
    )
    dep4_source_path = os.path.join(download_dir, "exsp-2.10.2-external-sha512-abcdefg.tgz")
    dep4_dest_path = os.path.join(
        download_dir, "external-exsp/exsp-2.10.2-external-sha512-abcdefg.tgz"
    )

    # This ensures that the bundled dependency is skipped
    assert len(mock_move.call_args_list) == 4

    mock_move.assert_has_calls(
        [
            mock.call(dep1_source_path, dep1_dest_path),
            mock.call(dep2_source_path, dep2_dest_path),
            mock.call(dep3_source_path, dep3_dest_path),
            mock.call(dep4_source_path, dep4_dest_path),
        ]
    )


@mock.patch("tempfile.TemporaryDirectory")
@mock.patch("os.path.exists")
@mock.patch("cachito.workers.pkg_managers.general_js.async_download_binary_file")
@mock.patch("shutil.move")
@mock.patch("cachito.workers.paths.get_worker_config")
def test_download_dependencies_skip_deps(
    mock_gwc,
    mock_move,
    mock_async_download_binary_file,
    mock_exists,
    mock_td,
    tmpdir,
):
    bundles_dir = tmpdir.mkdir("bundles")
    mock_gwc.return_value.cachito_bundles_dir = str(bundles_dir)
    mock_td.return_value.__enter__.return_value = str(tmpdir.mkdir("cachito-agfdsk"))
    mock_exists.return_value = False
    mock_async_download_binary_file.side_effect = lambda s, u, d, tarball_name, auth: tarball_name

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
            "bundled": False,
            "dev": False,
            "name": "rxjs",
            "version": "github:ReactiveX/rxjs#78032157f5c1655436829017bbda787565b48c30",
            "version_in_nexus": "6.5.5-external-gitcommit-78032157f5c1655436829017bbda787565b48c30",
        },
    ]

    request_id = 1
    proxy_repo_url = npm.get_npm_proxy_repo_url(request_id)
    download_dir = tmpdir.join("deps")
    download_dir.mkdir()

    general_js.download_dependencies(
        Path(download_dir), deps, proxy_repo_url, {"@angular/animations@8.2.14"}
    )

    # dep_1
    dep1_source_path = os.path.join(
        download_dir,
        "angular-devkit-architect-0.803.26.tgz",
    )
    dep1_dest_path = os.path.join(
        download_dir,
        "@angular-devkit/architect/angular-devkit-architect-0.803.26.tgz",
    )
    # dep_2
    dep2_source_path = os.path.join(
        download_dir,
        "rxjs-6.5.5-external-gitcommit-78032157f5c1655436829017bbda787565b48c30.tgz",
    )
    commit_hash = "-gitcommit-78032157f5c1655436829017bbda787565b48c30"
    dep2_dest_path = os.path.join(
        download_dir,
        f"github/ReactiveX/rxjs/rxjs-6.5.5-external{commit_hash}.tgz",
    )

    # This ensures that the skipped dependency is skipped
    assert len(mock_move.call_args_list) == 2

    mock_move.assert_has_calls(
        [
            mock.call(dep1_source_path, dep1_dest_path),
            mock.call(dep2_source_path, dep2_dest_path),
        ],
    )


@pytest.mark.parametrize(
    "data",
    [
        {
            "dep": "chai@4.2.0",
            "p_url": "http://nexus:8081/repository/1/chai/-/chai-4.2.0.tgz",
            "tarball_name": "chai-4.2.0.tgz",
        },
        {
            "dep": "assertion-error@1.1.0",
            "p_url": "http://nexus:8081/repository/1/assertion-error/-/assertion-error-1.1.0.tgz",
            "tarball_name": "assertion-error-1.1.0.tgz",
        },
        {
            "dep": "@fake_dep@4.2.0",
            "p_url": "http://nexus:8081/repository/1/@fake_dep/-/@fake_dep-4.2.0.tgz",
            "tarball_name": "fake_dep-4.2.0.tgz",
        },
    ],
)
def test_parse_dependency(data):
    proxy_repo_url = "http://nexus:8081/repository/1/"

    result = general_js.parse_dependency(proxy_repo_url, data["dep"])
    assert result == (data["p_url"], data["tarball_name"])


@mock.patch("cachito.workers.pkg_managers.general_js.async_download_binary_file")
@pytest.mark.asyncio
async def test_get_dependecies(mock_async_download_binary_file):
    mock_async_download_binary_file.side_effect = lambda s, u, d, tarball_name, auth: tarball_name

    deps_list = [
        "assertion-error@1.1.0",
        "chai@4.2.0",
        "check-error@1.0.2",
        "deep-eql@3.0.1",
        "fecha@4.2.0-external-sha1-f09ea0b8115b9733dddc88227086c73ba4ddc926",
        "get-func-name@2.0.0",
        "is-positive@3.1.0",
        "pathval@1.1.1",
        "type-detect@4.0.8",
    ]

    expected_result = [
        "assertion-error-1.1.0.tgz",
        "chai-4.2.0.tgz",
        "check-error-1.0.2.tgz",
        "deep-eql-3.0.1.tgz",
        "fecha-4.2.0-external-sha1-f09ea0b8115b9733dddc88227086c73ba4ddc926.tgz",
        "get-func-name-2.0.0.tgz",
        "is-positive-3.1.0.tgz",
        "pathval-1.1.1.tgz",
        "type-detect-4.0.8.tgz",
    ]

    conf = get_worker_config()
    result = await general_js.get_dependencies(
        "http://nexus:8081/repository/cachito-yarn-53/",
        "/tmp/cachito-archives/bundles/temp/53/deps/yarn",
        deps_list,
        conf.cachito_js_concurrency_limit,
        conf.cachito_nexus_username,
        conf.cachito_nexus_password,
    )

    assert result == expected_result


@mock.patch("cachito.workers.pkg_managers.general_js.nexus.execute_script")
def test_finalize_nexus_for_js_request(mock_exec_script):
    password = general_js.finalize_nexus_for_js_request("cachito-npm-1", "cachito-npm-1")

    mock_exec_script.assert_called_once()
    assert mock_exec_script.call_args[0][0] == "js_after_content_staged"
    payload = mock_exec_script.call_args[0][1]
    assert len(payload["password"]) >= 24
    assert payload["password"] == password
    assert payload.keys() == {"password", "repository_name", "username"}
    assert payload["repository_name"] == "cachito-npm-1"
    assert payload["username"] == "cachito-npm-1"


@mock.patch("cachito.workers.pkg_managers.general_js.nexus.execute_script")
def test_finalize_nexus_for_js_request_failed(mock_exec_script):
    mock_exec_script.side_effect = NexusScriptError()

    expected = (
        "Failed to configure Nexus to allow the request's npm repository to be ready for "
        "consumption"
    )
    with pytest.raises(NexusError, match=expected):
        general_js.finalize_nexus_for_js_request("cachito-npm-1", "cachito-npm-1")


def test_find_package_json(tmpdir):
    tarfile_path = os.path.join(tmpdir, "npm-package.tgz")
    with tarfile.open(tarfile_path, "x:gz") as archive:
        archive.addfile(tarfile.TarInfo("in/a/galaxy/far/far/away/package.json"))
        archive.addfile(tarfile.TarInfo("in/too/deep/package.json"))
        archive.addfile(tarfile.TarInfo("wrong_file.json"))
        archive.addfile(tarfile.TarInfo("package/index.html"))
        archive.addfile(tarfile.TarInfo("package2/package.json"))
        archive.addfile(tarfile.TarInfo("package/package.json"))
        assert general_js._find_package_json(archive) == "package2/package.json"


def test_find_package_json_no_package_json(tmpdir):
    tarfile_path = os.path.join(tmpdir, "random.tgz")
    with tarfile.open(tarfile_path, "x:gz") as archive:
        archive.addfile(tarfile.TarInfo("wrong_file.json"), b"{}")
        archive.addfile(
            tarfile.TarInfo("package/tom_hanks_quotes.html"),
            b"<p>Life is like a box of chocolates. You never know what you're gonna get.<p>",
        )
        assert general_js._find_package_json(archive) is None


@pytest.mark.parametrize("custom_ca_path", (None, "./registry-ca.pem"))
def test_generate_npmrc_content(custom_ca_path):
    npm_rc = general_js.generate_npmrc_content(
        "http://nexus:8081/repository/cachito-npm-1/",
        "admin",
        "admin123",
        custom_ca_path=custom_ca_path,
    )

    expected = textwrap.dedent(
        """\
        registry=http://nexus:8081/repository/cachito-npm-1/
        email=noreply@domain.local
        always-auth=true
        //nexus:8081/repository/cachito-npm-1/:_auth=YWRtaW46YWRtaW4xMjM=
        fetch-retries=5
        fetch-retry-factor=2
        strict-ssl=true
        """
    )

    if custom_ca_path:
        expected += f'cafile="{custom_ca_path}"\n'

    assert npm_rc == expected


def test_get_js_hosted_repo_name():
    assert general_js.get_js_hosted_repo_name() == "cachito-js-hosted"


@pytest.mark.parametrize("group", ("@reactive", None))
@pytest.mark.parametrize("repository", ("cachito-js-hosted", "cachito-yarn-1"))
@pytest.mark.parametrize("is_hosted", (True, False))
@mock.patch("cachito.workers.pkg_managers.general_js.nexus.get_component_info_from_nexus")
def test_get_js_component_info_from_nexus(mock_gcifn, group, repository, is_hosted):
    if group:
        identifier = f"{group}/rxjs"
    else:
        identifier = "rxjs"

    component = {
        "id": "Y2FjaGl0by1qcy1ob3N0ZWQ6ZDQ4MTE3NTQxZGNiODllYzYxM2IyMzk3MzIwMWQ3YmE",
        "repository": repository,
        "format": "npm",
        "group": group[1:] if group else None,
        "name": "rxjs",
        "version": "6.5.5-external-gitcommit-78032157f5c1655436829017bbda787565b48c30",
    }
    mock_gcifn.return_value = component

    rv = general_js._get_js_component_info_from_nexus(
        identifier,
        "6.5.5-external-gitcommit-78032157f5c1655436829017bbda787565b48c30",
        repository,
        is_hosted,
        max_attempts=3,
    )

    assert rv == component
    if group:
        mock_gcifn.assert_called_once_with(
            repository,
            "npm",
            "rxjs",
            "6.5.5-external-gitcommit-78032157f5c1655436829017bbda787565b48c30",
            "reactive",
            3,
            from_nexus_hoster=is_hosted,
        )
    else:
        mock_gcifn.assert_called_once_with(
            repository,
            "npm",
            "rxjs",
            "6.5.5-external-gitcommit-78032157f5c1655436829017bbda787565b48c30",
            nexus.NULL_GROUP,
            3,
            from_nexus_hoster=is_hosted,
        )


@mock.patch("cachito.workers.pkg_managers.general_js.get_js_hosted_repo_name")
@mock.patch("cachito.workers.pkg_managers.general_js._get_js_component_info_from_nexus")
def test_get_npm_component_info_from_nexus(mock_get_js_component, mock_get_hosted_repo):
    mock_get_hosted_repo.return_value = "cachito-js-hosted"

    general_js.get_npm_component_info_from_nexus("foo", "1.0.0-external", max_attempts=5)

    mock_get_hosted_repo.assert_called_once()
    mock_get_js_component.assert_called_once_with(
        "foo", "1.0.0-external", "cachito-js-hosted", is_hosted=True, max_attempts=5
    )


@mock.patch("cachito.workers.pkg_managers.general_js._get_js_component_info_from_nexus")
def test_get_yarn_component_info_from_non_hosted_nexus(mock_get_js_component):
    general_js.get_yarn_component_info_from_non_hosted_nexus(
        "foo", "1.0.0-external", "cachito-yarn-1", max_attempts=5
    )
    mock_get_js_component.assert_called_once_with(
        "foo", "1.0.0-external", "cachito-yarn-1", is_hosted=False, max_attempts=5
    )


@mock.patch("cachito.workers.pkg_managers.general_js.nexus.execute_script")
def test_prepare_nexus_for_js_request(mock_exec_script):
    general_js.prepare_nexus_for_js_request("cachito-npm-1")

    mock_exec_script.assert_called_once()
    assert mock_exec_script.call_args[0][0] == "js_before_content_staged"
    payload = mock_exec_script.call_args[0][1]
    assert payload == {
        "repository_name": "cachito-npm-1",
        "http_password": "cachito_unprivileged",
        "http_username": "cachito_unprivileged",
        "npm_proxy_url": "http://localhost:8081/repository/cachito-js/",
    }


@mock.patch("cachito.workers.pkg_managers.general_js.nexus.execute_script")
def test_prepare_nexus_for_js_request_failed(mock_exec_script):
    mock_exec_script.side_effect = NexusScriptError()

    expected = "Failed to prepare Nexus for Cachito to stage JavaScript content"
    with pytest.raises(NexusError, match=expected):
        _, password = general_js.prepare_nexus_for_js_request(1)


@mock.patch("cachito.workers.pkg_managers.general_js.verify_checksum")
@mock.patch("cachito.workers.pkg_managers.general_js.tempfile.TemporaryDirectory")
@mock.patch("cachito.workers.pkg_managers.general_js.run_cmd")
@mock.patch("cachito.workers.pkg_managers.general_js.nexus.upload_asset_only_component")
@pytest.mark.parametrize("checksum_info", [None, general.ChecksumInfo("sha512", "12345")])
def test_upload_non_registry_dependency(
    mock_ua, mock_run_cmd, mock_td, mock_vc, checksum_info, tmpdir
):
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

    general_js.upload_non_registry_dependency(
        "star-wars@5.0.0", "-the-empire-strikes-back", checksum_info=checksum_info
    )

    modified_tarfile_path = os.path.join(tmpdir, "modified-star-wars-5.0.0.tgz")
    with tarfile.open(modified_tarfile_path, "r:*") as f:
        # Verify that the archive has the original members in order
        members = {m.path for m in f.getmembers()}
        assert members == {"package/salutation.html", "package/package.json"}
        # Verify that the archive had its version updated
        new_version = json.load(f.extractfile("package/package.json"))["version"]
        assert new_version == "5.0.0-the-empire-strikes-back"

    mock_run_cmd.assert_called_once_with(["npm", "pack", "star-wars@5.0.0"], mock.ANY, mock.ANY)
    mock_ua.assert_called_once_with("cachito-js-hosted", "npm", modified_tarfile_path)
    if not checksum_info:
        mock_vc.assert_not_called()
    else:
        mock_vc.assert_called_once_with(tarfile_path, checksum_info)


@mock.patch("cachito.workers.pkg_managers.general_js.tempfile.TemporaryDirectory")
@mock.patch("cachito.workers.pkg_managers.general_js.run_cmd")
def test_upload_non_registry_dependency_invalid_prepare_script(mock_run_cmd, mock_td, tmpdir):
    tarfile_path = os.path.join(tmpdir, "star-wars-5.0.0.tgz")
    with tarfile.open(tarfile_path, "x:gz") as archive:
        tar_info = tarfile.TarInfo("package/fair-warning.html")
        content = "<h1>Je vais te détruire monsieur Solo!</h1>".encode("utf-8")
        tar_info.size = len(content)
        archive.addfile(tar_info, io.BytesIO(content))

        tar_info = tarfile.TarInfo("package/package.json")
        content = b'{"version": "5.0.0", "scripts": {"prepare": "rm -rf /"}}'
        tar_info.size = len(content)
        archive.addfile(tar_info, io.BytesIO(content))

    mock_td.return_value.__enter__.return_value = str(tmpdir)
    mock_run_cmd.return_value = "star-wars-5.0.0.tgz\n"

    expected = (
        "The dependency star-wars@5.0.0 is not supported because Cachito cannot execute the "
        "following required scripts of Git dependencies: prepack, prepare"
    )
    with pytest.raises(UnsupportedFeature, match=expected):
        general_js.upload_non_registry_dependency(
            "star-wars@5.0.0", "-the-empire-strikes-back", verify_scripts=True
        )


@mock.patch("cachito.workers.pkg_managers.general_js.tempfile.TemporaryDirectory")
@mock.patch("cachito.workers.pkg_managers.general_js.run_cmd")
def test_upload_non_registry_dependency_no_package_json(
    mock_run_cmd: mock.Mock,
    mock_td: mock.Mock,
    tmp_path: Path,
) -> None:
    mock_td.return_value.__enter__.return_value = str(tmp_path)
    mock_run_cmd.return_value = "star-wars-5.0.0.tgz\n"

    with tarfile.open(tmp_path / "star-wars-5.0.0.tgz", "w:gz") as archive:
        archive.addfile(
            tarfile.TarInfo("package/script.txt"),
            io.BytesIO(b"No. *I* am your father."),
        )

    expected = "Could not process dependency star-wars@5.0.0: no package.json file"
    with pytest.raises(FileAccessError, match=expected):
        general_js.upload_non_registry_dependency("star-wars@5.0.0", "-the-empire-strikes-back")


@mock.patch("cachito.workers.pkg_managers.general_js.tempfile.TemporaryDirectory")
@mock.patch("cachito.workers.pkg_managers.general_js.run_cmd")
@mock.patch("cachito.workers.pkg_managers.general_js.nexus.upload_asset_only_component")
def test_upload_non_registry_dependency_invalid_package_json(
    mock_ua, mock_run_cmd, mock_td, tmpdir
):
    tarfile_path = os.path.join(tmpdir, "star-wars-5.0.0.tgz")
    with tarfile.open(tarfile_path, "x:gz") as archive:
        tar_info = tarfile.TarInfo("package/package.json")
        content = b"Not JSON!"
        tar_info.size = len(content)
        archive.addfile(tar_info, io.BytesIO(content))

    mock_td.return_value.__enter__.return_value = str(tmpdir)
    mock_run_cmd.return_value = "star-wars-5.0.0.tgz\n"

    expected = "Could not process dependency star-wars@5.0.0: package.json is not valid JSON"
    with pytest.raises(FileAccessError, match=expected):
        general_js.upload_non_registry_dependency("star-wars@5.0.0", "-the-empire-strikes-back")


@pytest.mark.parametrize("exists", (False, True))
@mock.patch("cachito.workers.pkg_managers.general_js.get_npm_component_info_from_nexus")
@mock.patch("cachito.workers.pkg_managers.general_js.upload_non_registry_dependency")
def test_process_non_registry_dependency_github(mock_unrd, mock_gncifn, exists):
    checksum = (
        "325f07861e0ab888d90606b1074fde956fd3954dcc4c6e418dbff9d8aa8342b5507481408832bfaac8e48f344"
        "dc650c8df0f8182c0271ed9fa233aa32c329839"
    )
    # The information returned from Nexus of the uploaded component
    nexus_component_info = {
        "assets": [
            {
                "checksum": {"sha512": checksum},
                "downloadUrl": (
                    "https://nexus.domain.local/repository/cachito-js-hosted/rxjs/-/"
                    "rxjs-6.5.5-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b.tgz"
                ),
            }
        ],
        "version": "6.5.5-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5",
    }
    if exists:
        mock_gncifn.return_value = nexus_component_info
    else:
        mock_gncifn.side_effect = [None, nexus_component_info]

    dep_name = "rxjs"
    # The information from the lock file
    dep_info = {
        "version": "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5",
        "from": "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5",
        "requires": {"tslib": "^1.9.0"},
    }

    dep = general_js.JSDependency(name=dep_name, source=dep_info["version"])
    new_dep = general_js.process_non_registry_dependency(dep)

    # Verify the information to update the lock file with is correct
    assert new_dep == general_js.JSDependency(
        name=dep.name,
        source=(
            "https://nexus.domain.local/repository/cachito-js-hosted/rxjs/-/rxjs-6.5.5-"
            "external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b.tgz"
        ),
        version="6.5.5-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5",
        integrity=(
            "sha512-Ml8Hhh4KuIjZBgaxB0/elW/TlU3MTG5Bjb/52KqDQrVQdIFAiDK/qsjkjzRNxlDI3w+BgsAnHtn6I"
            "zqjLDKYOQ=="
        ),
    )
    if exists:
        mock_gncifn.assert_called_once_with(
            "rxjs", "*-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5"
        )
        # Verify no upload occurs when the component already exists in Nexus
        mock_unrd.assert_not_called()
    else:
        assert mock_gncifn.call_count == 2
        mock_gncifn.assert_has_calls(
            [
                mock.call("rxjs", "*-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5"),
                mock.call(
                    "rxjs",
                    "*-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5",
                    max_attempts=5,
                ),
            ]
        )
        mock_unrd.assert_called_once_with(
            "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5",
            "-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5",
            True,
            None,
        )


@pytest.mark.parametrize("exists", (False, True))
@mock.patch("cachito.workers.pkg_managers.general_js.get_npm_component_info_from_nexus")
@mock.patch("cachito.workers.pkg_managers.general_js.upload_non_registry_dependency")
def test_process_non_registry_dependency_http(mock_unrd, mock_gncifn, exists):
    checksum = (
        "325f07861e0ab888d90606b1074fde956fd3954dcc4c6e418dbff9d8aa8342b5507481408832bfaac8e48f344"
        "dc650c8df0f8182c0271ed9fa233aa32c329839"
    )
    nexus_component_info = {
        "assets": [
            {
                "checksum": {"sha512": checksum},
                "downloadUrl": (
                    "https://nexus.domain.local/repository/cachito-js-hosted/rxjs/-/"
                    "rxjs-6.5.5-external-sha512-325f07861e0ab888d90606b1074fde956fd3954dcc4c6e418d"
                    "bff9d8aa8342b5507481408832bfaac8e48f344.tgz"
                ),
            }
        ],
        "version": (
            "6.5.5-external-sha512-325f07861e0ab888d90606b1074fde956fd3954dcc4c6e418dbff9d8aa8342"
            "b5507481408832bfaac8e48f344"
        ),
    }
    if exists:
        mock_gncifn.return_value = nexus_component_info
    else:
        mock_gncifn.side_effect = [None, nexus_component_info]

    dep_name = "rxjs"
    dep_info = {
        "version": "https://github.com/ReactiveX/rxjs/archive/6.5.5.tar.gz",
        "requires": {"tslib": "^1.9.0"},
        "integrity": (
            "sha512-Ml8Hhh4KuIjZBgaxB0/elW/TlU3MTG5Bjb/52KqDQrVQdIFAiDK/qsjkjzRNxlDI3w+BgsAnHtn6I"
            "zqjLDKYOQ=="
        ),
    }

    dep = general_js.JSDependency(
        name=dep_name, source=dep_info["version"], integrity=dep_info["integrity"]
    )
    new_dep = general_js.process_non_registry_dependency(dep)

    assert new_dep == general_js.JSDependency(
        name=dep.name,
        source=(
            "https://nexus.domain.local/repository/cachito-js-hosted/rxjs/-/rxjs-6.5.5-"
            "external-sha512-325f07861e0ab888d90606b1074fde956fd3954dcc4c6e418dbff9d8aa8342b55074"
            "81408832bfaac8e48f344.tgz"
        ),
        version=(
            "6.5.5-external-sha512-325f07861e0ab888d90606b1074fde956fd3954dcc4c6e418dbff9d8"
            "aa8342b5507481408832bfaac8e48f344"
        ),
        integrity=(
            "sha512-Ml8Hhh4KuIjZBgaxB0/elW/TlU3MTG5Bjb/52KqDQrVQdIFAiDK/qsjkjzRNxlDI3w+BgsAnHtn6I"
            "zqjLDKYOQ=="
        ),
    )

    suffix = (
        "-external-sha512-325f07861e0ab888d90606b1074fde956fd3954dcc4c6e418dbff9d8aa8342b5"
        "507481408832bfaac8e48f344dc650c8df0f8182c0271ed9fa233aa32c329839"
    )

    suffix_search = f"*{suffix}"
    if exists:
        mock_gncifn.assert_called_once_with("rxjs", suffix_search)
        # Verify no upload occurs when the component already exists in Nexus
        mock_unrd.assert_not_called()
    else:
        assert mock_gncifn.call_count == 2
        mock_gncifn.assert_has_calls(
            [mock.call("rxjs", suffix_search), mock.call("rxjs", suffix_search, max_attempts=5)]
        )
        mock_unrd.assert_called_once_with(
            "https://github.com/ReactiveX/rxjs/archive/6.5.5.tar.gz",
            suffix,
            False,
            general.ChecksumInfo(
                "sha512",
                "325f07861e0ab888d90606b1074fde956fd3954dcc4c6e418dbff9d8aa8342b5507481408832bf"
                "aac8e48f344dc650c8df0f8182c0271ed9fa233aa32c329839",
            ),
        )


def test_process_non_registry_dependency_http_integrity_missing():
    dep_identifier = "https://github.com/ReactiveX/rxjs/archive/6.5.5.tar.gz"
    dep_name = "rxjs"
    dep_info = {
        "version": dep_identifier,
        "requires": {"tslib": "^1.9.0"},
    }
    dep = general_js.JSDependency(dep_name, source=dep_info["version"])

    expected = (
        f"The dependency {dep_name}@{dep_identifier} is missing the integrity value. "
        'Is the "integrity" key missing in your lockfile?'
    )
    with pytest.raises(InvalidFileFormat, match=expected):
        general_js.process_non_registry_dependency(dep)


def test_process_non_registry_dependency_invalid_location():
    dep_identifier = "file:rxjs-6.5.5.tar.gz"
    dep_name = "rxjs"
    dep_info = {
        "version": dep_identifier,
        "requires": {"tslib": "^1.9.0"},
    }
    dep = general_js.JSDependency(dep_name, source=dep_info["version"])

    expected = f"The dependency {dep_name}@{dep_identifier} is hosted in an unsupported location"
    with pytest.raises(UnsupportedFeature, match=expected):
        general_js.process_non_registry_dependency(dep)


@mock.patch("cachito.workers.pkg_managers.general_js.get_npm_component_info_from_nexus")
@mock.patch("cachito.workers.pkg_managers.general_js.upload_non_registry_dependency")
def test_process_non_registry_dependency_github_not_in_nexus(mock_unrd, mock_gncifn):
    mock_gncifn.return_value = None

    dep_identifier = "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5"
    dep_name = "rxjs"
    dep_info = {
        "version": dep_identifier,
        "from": "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5",
        "requires": {"tslib": "^1.9.0"},
    }
    dep = general_js.JSDependency(dep_name, source=dep_info["version"])

    expected = (
        f"The dependency {dep_name}@{dep_identifier} was uploaded to Nexus but is not accessible"
    )
    with pytest.raises(FileAccessError, match=expected):
        general_js.process_non_registry_dependency(dep)


@pytest.mark.parametrize(
    "test_input",
    [
        {
            "version": "file:packages/a",
            "workspaces": ["a", "b"],
            "is_allowed": False,
        },
        {
            "version": "file:c",
            "workspaces": ["a", "b"],
            "is_allowed": False,
        },
        {
            "version": "file:a",
            "workspaces": ["a", "b"],
            "is_allowed": True,
        },
        {
            "version": "file:packages/a",
            "workspaces": ["./packages/a"],
            "is_allowed": True,
        },
        {
            "version": "file:packages/a",
            "workspaces": ["./packages/*"],
            "is_allowed": True,
        },
        {
            "version": "git+https://...",
            "workspaces": [],
            "is_allowed": True,
        },
    ],
)
def test_vet_file_dependency_workspaces(test_input: dict[str, Any]) -> None:
    js_dep = general_js.JSDependency(name="name", source=test_input["version"])

    if test_input["is_allowed"]:
        general_js.vet_file_dependency(js_dep, test_input["workspaces"], allowlist=[])
    else:
        with pytest.raises(
            InvalidRepoStructure,
            match=r"name@file:.* is a 'file:' dependency. File dependencies are allowed if: .*",
        ):
            general_js.vet_file_dependency(js_dep, test_input["workspaces"], allowlist=[])


@pytest.mark.parametrize(
    "test_input",
    [
        {
            "name": "allowed_dependency",
            "version": "file:packages/allowed_dependency",
            "is_allowed": True,
        },
        {
            "name": "disallowed_dependency",
            "version": "file:packages/disallowed_dependency",
            "is_allowed": False,
        },
        {
            "name": "not_a_file_dependency",
            "version": "git+https://...",
            "is_allowed": True,
        },
    ],
)
def test_vet_file_dependency_allowlist(test_input: dict[str, Any]) -> None:
    allowlist = {"allowed_dependency"}
    js_dep = general_js.JSDependency(name=test_input["name"], source=test_input["version"])

    if test_input["is_allowed"]:
        general_js.vet_file_dependency(js_dep, workspaces=[], allowlist=allowlist)
    else:
        with pytest.raises(
            InvalidRepoStructure,
            match=r".*@file:.* is a 'file:' dependency. File dependencies are allowed if: .*",
        ):
            general_js.vet_file_dependency(js_dep, workspaces=[], allowlist=[])


@pytest.mark.parametrize(
    "checksum, algorithm, expected",
    [
        (
            (
                "325f07861e0ab888d90606b1074fde956fd3954dcc4c6e418dbff9d8aa8342b5507481408832bfaac"
                "8e48f344dc650c8df0f8182c0271ed9fa233aa32c329839"
            ),
            "sha512",
            (
                "sha512-Ml8Hhh4KuIjZBgaxB0/elW/TlU3MTG5Bjb/52KqDQrVQdIFAiDK/qsjkjzRNxlDI3w+BgsAnHt"
                "n6IzqjLDKYOQ=="
            ),
        ),
        ("a" * 40, "sha1", "sha1-qqqqqqqqqqqqqqqqqqqqqqqqqqo="),
    ],
)
def test_convert_hex_sha_to_npm(checksum, algorithm, expected):
    assert general_js.convert_hex_sha_to_npm(checksum, algorithm) == expected


def convert_integrity_to_hex_checksum():
    integrity = (
        "sha512-Ml8Hhh4KuIjZBgaxB0/elW/TlU3MTG5Bjb/52KqDQrVQdIFAiDK/qsjkjzRNxlDI3w+BgsAnHtn6Izqj"
        "LDKYOQ=="
    )

    rv = general_js.convert_integrity_to_hex_checksum(integrity)

    expected = (
        "325f07861e0ab888d90606b1074fde956fd3954dcc4c6e418dbff9d8aa8342b5507481408832bfaac8e48f344"
        "dc650c8df0f8182c0271ed9fa233aa32c329839"
    )
    assert rv == expected
