# SPDX-License-Identifier: GPL-3.0-or-later

from os import path
import shutil
import subprocess
import tarfile

import pytest

import utils


@pytest.mark.skipif(not shutil.which("go"), reason="requires go to be installed")
def test_run_app_from_bundle(test_env, default_request, tmpdir):
    """
    Check that downloaded bundle could be used to run the application.

    Process:
    * Send new request to Cachito API
    * Download a bundle from the request
    * Run go build
    * Run the application

    Checks:
    * Check that the state of request is complete
    * Check that the bundle is properly downloaded
    * Check that the application runs successfully
    """
    response = default_request["gomod"].complete_response
    assert response.status == 200
    assert response.data["state"] == "complete"

    file_name_tar = tmpdir.join(f"download_{str(response.id)}.tar.gz")
    bundle_dir = tmpdir.mkdir(f"download_{str(response.id)}")
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"])
    resp = client.download_bundle(response.id, file_name_tar)
    assert resp.status == 200
    assert tarfile.is_tarfile(file_name_tar)

    with tarfile.open(file_name_tar, "r:gz") as tar:
        tar.extractall(bundle_dir)

    app_name = test_env["run_app"]["app_name"]
    app_binary_file = str(tmpdir.join(app_name))
    subprocess.run(
        ["go", "build", "-o", app_binary_file, str(bundle_dir.join("app", "main.go"))],
        env={
            "GOPATH": str(bundle_dir.join("deps", "gomod")),
            "GOCACHE": str(bundle_dir.join("deps", "gomod")),
        },
        cwd=str(bundle_dir.join("app")),
        check=True,
    )

    assert path.exists(app_binary_file)
    sp = subprocess.run([app_binary_file, "--help"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert sp.returncode == 0
