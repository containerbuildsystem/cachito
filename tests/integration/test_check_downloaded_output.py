# SPDX-License-Identifier: GPL-3.0-or-later

from os import path
import tarfile
from pathlib import Path

import utils


def test_check_downloaded_output(test_env, tmpdir):
    """
    Check that the bundle has all the necessities.

    Process:
    * Send new request to Cachito API
    * Send request to download appropriate bundle from Cachito

    Checks:
    * Check that response code is 200
    * Check that state is "complete"
    * Check the downloaded data are in gzip format and valid
    * Check that dir deps/gomod/… contains cached dependencies
    * Check that dir app/ contains application source code
    * Check that the same full path filename is not duplicated
    """

    client = utils.Client(test_env["api_url"], test_env["api_auth_type"])
    response = client.create_new_request(
        payload={
            "repo": test_env["download_output"]["repo"],
            "ref": test_env["download_output"]["ref"],
            "pkg_managers": test_env["download_output"]["pkg_managers"],
        },
    )

    assert response.status == 201
    response = client.wait_for_complete_request(response)
    assert response.data["state"] == "complete"

    file_name = tmpdir.join(f"download_{str(response.id)}")
    file_name_tar = tmpdir.join(f"download_{str(response.id)}.tar.gz")

    resp = client.download_bundle(response.id, file_name_tar)
    assert resp.status == 200
    assert tarfile.is_tarfile(file_name_tar)

    with tarfile.open(file_name_tar, "r:gz") as tar:
        tar.extractall(file_name)

    dependencies_path = "deps/gomod/pkg/mod/cache/download/"
    names = [i["name"] for i in response.data["dependencies"]]
    for dependency in names:
        package_name = utils.escape_path_go(dependency)
        dependency_path = path.join(file_name, dependencies_path, package_name)
        assert path.exists(dependency_path)

    assert path.exists(f"{file_name}/app/go.mod")
    with open(f"{file_name}/app/go.mod", "r") as file:
        module_names = []
        for line in file:
            if line.startswith("module "):
                module_names.append(line.split()[-1])
                break
        assert module_names == [i["name"] for i in response.data["packages"]]

    list_go_files = []
    for app_path in Path(f"{file_name}/app/").rglob("*.go"):
        list_go_files.append(app_path)
    assert len(list_go_files) > 0

    with tarfile.open(file_name_tar, mode="r:gz") as tar:
        members = tar.getmembers()
        path_names = set()
        for dependency in members:
            assert dependency.name not in path_names
            path_names.add(dependency.name)
