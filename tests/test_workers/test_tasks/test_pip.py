# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import json
import os
from unittest import mock

import pytest

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config
from cachito.workers.paths import RequestBundleDir
from cachito.workers.tasks import pip


@mock.patch("cachito.workers.tasks.pip.nexus.execute_script")
def test_cleanup_pip_request(mock_exec_script):
    pip.cleanup_pip_request(42)

    expected_payload = {
        "pip_repository_name": "cachito-pip-hosted-42",
        "raw_repository_name": "cachito-pip-raw-42",
        "username": "cachito-pip-42",
    }
    mock_exec_script.assert_called_once_with("pip_cleanup", expected_payload)


@pytest.mark.parametrize("with_cert", [True, False])
@pytest.mark.parametrize("with_req", [True, False])
@pytest.mark.parametrize("package_subpath", [None, ".", "some/path"])
@mock.patch("cachito.workers.tasks.pip.resolve_pip")
@mock.patch("cachito.workers.tasks.pip.finalize_nexus_for_pip_request")
@mock.patch("cachito.workers.tasks.pip.prepare_nexus_for_pip_request")
@mock.patch("cachito.workers.tasks.pip.set_request_state")
@mock.patch("cachito.workers.tasks.pip.get_request")
@mock.patch("cachito.workers.tasks.pip.update_request_with_deps")
@mock.patch("cachito.workers.tasks.pip.update_request_with_package")
@mock.patch("cachito.workers.tasks.pip.update_request_with_config_files")
@mock.patch("cachito.workers.tasks.pip.nexus.get_ca_cert")
@mock.patch("cachito.workers.tasks.pip.PipRequirementsFile._read_lines")
@mock.patch("cachito.workers.tasks.pip.nexus.get_raw_component_asset_url")
def test_fetch_pip_source(
    mock_get_raw_asset_url,
    mock_read,
    mock_cert,
    mock_update_cfg,
    mock_update_pkg,
    mock_update_deps,
    mock_get_request,
    mock_set_state,
    mock_prepare_nexus,
    mock_finalize_nexus,
    mock_resolve,
    with_cert,
    with_req,
    package_subpath,
    tmp_path,
    task_passes_state_check,
):
    pkg_data = {
        "package": {"name": "foo", "version": "1", "type": "pip"},
        "dependencies": [{"name": "bar", "version": "2.0", "type": "pip", "dev": True}],
        "requirements": [],
    }
    request = {"id": 1}
    username = f"cachito-pip-{request['id']}"
    password = "password"
    repo_name = f"cachito-pip-hosted-{request['id']}"
    config = get_worker_config()
    nexus_url = config.cachito_nexus_url
    index_base_url = nexus_url.replace("://", f"://{username}:{password}@")
    env_vars = {
        "PIP_INDEX_URL": {
            "value": f"{index_base_url}/repository/{repo_name}/simple",
            "kind": "literal",
        }
    }
    mock_cert.return_value = None
    cert_contents = "stub_cert"
    cfg_contents = []
    if with_req:
        requirements_path = (
            RequestBundleDir(1).app_subpath(package_subpath or ".").source_dir / "requirements.txt"
        )
        pkg_data["requirements"].append(str(requirements_path))
        mock_get_raw_asset_url.return_value = "http://fake-raw-asset-url.dev"
        req_contents = f"mypkg @ git+https://www.github.com/cachito/mypkg.git@{'f'*40}?egg=mypkg\n"
        mock_read.return_value = [req_contents]
        b64_req_contents = base64.b64encode(
            f"mypkg @ http://{username}:{password}@fake-raw-asset-url.dev".encode()
        ).decode()

        requirements_relpath = requirements_path.relative_to(RequestBundleDir(1))
        cfg_contents.append(
            {"content": b64_req_contents, "path": str(requirements_relpath), "type": "base64"}
        )
    if with_cert:
        mock_cert.return_value = cert_contents
        env_vars["PIP_CERT"] = {"value": "app/package-index-ca.pem", "kind": "path"}
        b64_cert_contents = base64.b64encode(cert_contents.encode()).decode()
        cfg_contents.append(
            {"content": b64_cert_contents, "path": "app/package-index-ca.pem", "type": "base64"}
        )

    mock_resolve.return_value = pkg_data
    mock_finalize_nexus.return_value = password
    mock_get_request.return_value = request

    if package_subpath:
        package_configs = [{"path": package_subpath}]
    else:
        package_configs = None

    pip.fetch_pip_source(request["id"], package_configs=package_configs)

    mock_update_pkg.assert_called_once_with(
        request["id"], pkg_data["package"], env_vars, package_subpath=package_subpath or "."
    )
    mock_update_deps.assert_called_once_with(
        request["id"],
        pkg_data["package"],
        [{"name": "bar", "version": "2.0", "type": "pip", "dev": True}],
    )
    if cfg_contents:
        mock_update_cfg.assert_called_once_with(
            request["id"], cfg_contents,
        )

    expected = pkg_data["package"].copy()
    expected["dependencies"] = pkg_data["dependencies"]
    if package_subpath and package_subpath != os.curdir:
        expected["path"] = package_subpath
    assert {"packages": [expected]} == json.loads(
        RequestBundleDir(1).pip_packages_data.read_bytes()
    )


@pytest.mark.parametrize(
    "original, component_name",
    [
        ["foo==1\n", None],
        [
            f"mypkg @ git+https://www.github.com/cachito/mypkg.git@{'f'*40}?egg=mypkg\n",
            f"mypkg/mypkg-external-gitcommit-{'f'*40}.tar.gz",
        ],
        [
            "mypkg @ https://example.com/cachito/mypkg.tar.gz#egg=mypkg&cachito_hash=sha256%3Ax\n",
            "mypkg/mypkg-external-sha256-x.tar.gz",
        ],
    ],
)
@pytest.mark.parametrize("found_url", ["http://fake-resource.dev", None, "fake-resource.dev"])
@mock.patch("cachito.workers.tasks.pip.nexus.get_raw_component_asset_url")
def test_get_custom_requirement_config_file(
    mock_get_url, original, component_name, tmp_path, found_url
):
    if found_url:
        mock_get_url.return_value = found_url
    else:
        mock_get_url.return_value = None

    req_file = tmp_path / "req.txt"
    req_file.write_text(original)
    repo_name = "raw-1"
    username = "my_username"
    password = "my_password"
    if not found_url and component_name:
        msg = f"Could not retrieve URL for {component_name} in {repo_name}. Was the asset uploaded?"
        with pytest.raises(CachitoError, match=msg):
            pip._get_custom_requirement_config_file(
                req_file, tmp_path, repo_name, username, password
            )
    elif found_url and "://" not in found_url and component_name:
        msg = f"Nexus raw resource URL: {found_url} is not a valid URL"
        with pytest.raises(CachitoError, match=msg):
            pip._get_custom_requirement_config_file(
                req_file, tmp_path, repo_name, username, password
            )
    else:
        req = pip._get_custom_requirement_config_file(
            req_file, tmp_path, repo_name, username, password
        )
        if component_name:
            mock_get_url.assert_called_once_with(
                repo_name, component_name, max_attempts=5, from_nexus_hoster=False
            )
            assert req["type"] == "base64"
            assert req["path"] == "app/req.txt"
            final_url = found_url.replace("://", f"://{username}:{password}@")
            assert final_url in base64.b64decode(req["content"]).decode()
        else:
            mock_get_url.assert_not_called()
            assert req is None
