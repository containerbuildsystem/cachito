# SPDX-License-Identifier: GPL-3.0-or-later

import tarfile
from os import path
from pathlib import Path

from . import utils


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
        assert path.exists(
            dependency_path
        ), f"#{response.id}: Dependency path does not exist: {dependency_path}"

    go_mod_path = path.join(file_name, "app", "go.mod")
    assert path.exists(
        go_mod_path
    ), f"#{response.id}: File go.mod does not exist in location: {go_mod_path}"
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
            assert dependency.name not in path_names, (
                f"#{response.id}: There is an unexpected duplicate {dependency.name} "
                f"in archive {file_name_tar}"
            )
            path_names.add(dependency.name)


def test_git_dir_not_included_by_default(test_env, default_requests, tmpdir):
    """
    Check that the bundle does not include the .git file objects by default.

    Process:
    * Send new request to Cachito API
    * Send request to download appropriate bundle from Cachito

    Checks:
    * Check that response code is 200
    * Check that state is "complete"
    * Check the downloaded data are in gzip format and valid
    * Check that downloaded data does not contain any .git files
    """
    response = default_requests["gomod"].complete_response
    utils.assert_properly_completed_response(response)
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))

    client.download_and_extract_archive(response.id, tmpdir)
    file_name_tar = tmpdir.join(f"download_{str(response.id)}.tar.gz")

    with tarfile.open(file_name_tar, mode="r:gz") as tar:
        git_files = {
            member.name for member in tar.getmembers() if path.basename(member.name) == ".git"
        }

    assert not git_files, (
        f"#{response.id}: There are unexpected .git files in archive {file_name_tar}: "
        f"{git_files}"
    )


def test_git_dir_included_by_flag(test_env, tmpdir):
    """
    Check that the bundle includes the .git file objects when include-git-dir flag is used.

    Process:
    * Send new request to Cachito API
    * Send request to download appropriate bundle from Cachito

    Checks:
    * Check that response code is 200
    * Check that state is "complete"
    * Check the downloaded data are in gzip format and valid
    * Check that downloaded data contains app/.git file object, directory
    """
    package_info = test_env["packages"]["gomod"]
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    initial_response = client.create_new_request(
        payload={
            "repo": package_info["repo"],
            "ref": package_info["ref"],
            "pkg_managers": package_info["pkg_managers"],
            "flags": ["include-git-dir"],
        },
    )
    response = client.wait_for_complete_request(initial_response)
    utils.assert_properly_completed_response(response)

    client.download_and_extract_archive(response.id, tmpdir)
    file_name_tar = tmpdir.join(f"download_{str(response.id)}.tar.gz")

    with tarfile.open(file_name_tar, mode="r:gz") as tar:
        git_files = {
            member.name for member in tar.getmembers() if path.basename(member.name) == ".git"
        }

    assert git_files == {"app/.git"}, (
        f"#{response.id}: There are unexpected, or missing, .git files in archive {file_name_tar}: "
        f"{git_files}"
    )
