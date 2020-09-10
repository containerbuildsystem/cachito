# SPDX-License-Identifier: GPL-3.0-or-later
import base64
from unittest import mock

import pytest

from cachito.workers.config import get_worker_config
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
@mock.patch("cachito.workers.tasks.pip.resolve_pip")
@mock.patch("cachito.workers.tasks.pip.finalize_nexus_for_pip_request")
@mock.patch("cachito.workers.tasks.pip.prepare_nexus_for_pip_request")
@mock.patch("cachito.workers.tasks.pip.set_request_state")
@mock.patch("cachito.workers.tasks.pip.update_request_with_deps")
@mock.patch("cachito.workers.tasks.pip.update_request_with_package")
@mock.patch("cachito.workers.tasks.pip.update_request_with_config_files")
@mock.patch("cachito.workers.tasks.pip.nexus.get_ca_cert")
def test_fetch_pip_source(
    mock_cert,
    mock_update_cfg,
    mock_update_pkg,
    mock_update_deps,
    mock_set_state,
    mock_prepare_nexus,
    mock_finalize_nexus,
    mock_resolve,
    with_cert,
):
    pkg_data = {
        "package": {"name": "foo", "version": "1", "type": "pip"},
        "dependencies": [{"name": "bar", "version": "2.0", "type": "pip", "dev": True}],
    }
    request = {"id": 1}
    username = f"cachito-pip-{request['id']}"
    password = "password"
    repo_name = f"cachito-pip-hosted-{request['id']}"
    config = get_worker_config()
    nexus_url = config.cachito_nexus_url
    index_base_url = nexus_url.replace("://", f"://{username}:{password}@")
    env_vars = {
        "PIP_INDEX_URL": {"value": f"{index_base_url}/repository/{repo_name}/", "kind": "literal"}
    }
    mock_cert.return_value = None
    cert_contents = "stub_cert"
    if with_cert:
        mock_cert.return_value = cert_contents
        env_vars["PIP_CERT"] = {"value": "app/package-index-ca.pem", "kind": "path"}

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
    if with_cert:
        b64_cert_contents = base64.b64encode(cert_contents.encode()).decode()
        mock_update_cfg.assert_called_once_with(
            request["id"],
            [{"content": b64_cert_contents, "path": "app/package-index-ca.pem", "type": "base64"}],
        )
