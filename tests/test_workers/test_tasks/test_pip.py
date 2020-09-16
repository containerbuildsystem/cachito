# SPDX-License-Identifier: GPL-3.0-or-later
import base64
from unittest import mock

import pytest

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
@mock.patch("cachito.workers.tasks.pip.resolve_pip")
@mock.patch("cachito.workers.tasks.pip.finalize_nexus_for_pip_request")
@mock.patch("cachito.workers.tasks.pip.prepare_nexus_for_pip_request")
@mock.patch("cachito.workers.tasks.pip.set_request_state")
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
    mock_set_state,
    mock_prepare_nexus,
    mock_finalize_nexus,
    mock_resolve,
    with_cert,
    with_req,
    tmp_path,
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
        pkg_data["requirements"].append(str(RequestBundleDir(1).source_dir / "requirements.txt"))
        mock_get_raw_asset_url.return_value = "fake-raw-asset-url"
        req_contents = f"mypkg @ git+https://www.github.com/cachito/mypkg.git@{'f'*40}?egg=mypkg\n"
        mock_read.return_value = [req_contents]
        b64_req_contents = base64.b64encode("mypkg @ fake-raw-asset-url".encode()).decode()
        cfg_contents.append(
            {"content": b64_req_contents, "path": "app/requirements.txt", "type": "base64"}
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
    mock_set_state.return_value = request

    pip.fetch_pip_source(request["id"])

    mock_update_pkg.assert_called_once_with(request["id"], pkg_data["package"], env_vars)
    mock_update_deps.assert_called_once_with(
        request["id"],
        pkg_data["package"],
        [{"name": "bar", "version": "2.0", "type": "pip", "dev": True}],
    )
    if cfg_contents:
        mock_update_cfg.assert_called_once_with(
            request["id"], cfg_contents,
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
@mock.patch("cachito.workers.tasks.pip.nexus.get_raw_component_asset_url")
def test_get_custom_requirement_config_file(mock_get_url, original, component_name, tmp_path):
    new_url = "fake-resource"
    mock_get_url.return_value = new_url
    req_file = tmp_path / "req.txt"
    req_file.write_text(original)
    component_name = component_name
    repo_name = "raw-1"
    req = pip._get_custom_requirement_config_file(req_file, tmp_path, repo_name)
    if component_name:
        mock_get_url.assert_called_once_with(repo_name, component_name, max_attempts=5)
        assert req["type"] == "base64"
        assert req["path"] == "app/req.txt"
        assert new_url in base64.b64decode(req["content"]).decode()
    else:
        mock_get_url.assert_not_called()
        assert req is None
