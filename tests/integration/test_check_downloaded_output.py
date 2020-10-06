# SPDX-License-Identifier: GPL-3.0-or-later

from os import path
import tarfile
from pathlib import Path

import utils


def test_check_downloaded_output(test_env, default_requests, tmpdir):
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
    response = default_requests["gomod"].complete_response
    utils.assert_properly_completed_response(response)
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))

    file_name = tmpdir.join(f"download_{str(response.id)}")
    client.download_and_extract_archive(response.id, tmpdir)

    pkg_managers = test_env["downloaded_output"]["pkg_managers"]
    dependencies_path = path.join("deps", "gomod", "pkg", "mod", "cache", "download")
    names = [i["name"] for i in response.data["dependencies"] if i["type"] in pkg_managers]
    for dependency in names:
        package_name = utils.escape_path_go(dependency)
        dependency_path = path.join(file_name, dependencies_path, package_name)
        assert path.exists(dependency_path)

    go_mod_path = path.join(file_name, "app", "go.mod")
    assert path.exists(go_mod_path)
    with open(go_mod_path, "r") as file:
        module_names = []
        for line in file:
            if line.startswith("module "):
                module_names.append(line.split()[-1])
                break
        expected_packages = [
            i["name"] for i in response.data["packages"] if i["type"] in pkg_managers
        ]
        assert set(module_names) == set(expected_packages)

    list_go_files = []
    for app_path in Path(path.join(file_name, "app")).rglob("*.go"):
        list_go_files.append(app_path)
    assert len(list_go_files) > 0

    file_name_tar = tmpdir.join(f"download_{str(response.id)}.tar.gz")
    with tarfile.open(file_name_tar, mode="r:gz") as tar:
        members = tar.getmembers()
        path_names = set()
        for dependency in members:
            assert dependency.name not in path_names
            path_names.add(dependency.name)
