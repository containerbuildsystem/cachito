# SPDX-License-Identifier: GPL-3.0-or-later

from os import path

import utils


def test_dependency_replacement(test_env, tmpdir):
    """
    Check that proper versions of dependencies were used.

    Process:
    * Send new request to Cachito API to fetch retrodep with another version of dependency package
    * Download a bundle archive

    Checks:
    * Check that the state of request is complete
    * Check that in the response there is a key "replaces" with dict values which was replaced
    * Check that dir deps/gomod/pkg/mod/cache/download/github.com/pkg/errors/@v/… contains
        only the required version
    * Check that app/go.mod file has replace directive for the specified package
    """
    dependency_replacements = test_env["dep_replacement"]["dependency_replacements"]
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    response_created_req = client.create_new_request(
        payload={
            "repo": test_env["packages"]["gomod"]["repo"],
            "ref": test_env["packages"]["gomod"]["ref"],
            "pkg_managers": test_env["packages"]["gomod"]["pkg_managers"],
            "dependency_replacements": dependency_replacements,
        },
    )
    response = client.wait_for_complete_request(response_created_req)
    utils.assert_properly_completed_response(response)

    names_replaced_dependencies = {
        i["replaces"]["name"] for i in response.data["dependencies"] if i["replaces"] is not None
    }
    supposed_replaced_dependencies = set(i["name"] for i in dependency_replacements)
    assert names_replaced_dependencies == supposed_replaced_dependencies

    bundle_dir_name = tmpdir.join(f"download_{str(response.id)}")
    client.download_and_extract_archive(response.id, tmpdir)

    for dependency in dependency_replacements:
        dep_name = utils.escape_path_go(dependency["name"])
        dependency_version_file = path.join(
            bundle_dir_name,
            "deps",
            "gomod",
            "pkg",
            "mod",
            "cache",
            "download",
            dep_name,
            "@v",
            "list",
        )
        assert path.exists(dependency_version_file), (
            f"#{response.id}: Path for version of dependency "
            f"{dep_name} does not exist: {dependency_version_file}"
        )
        with open(dependency_version_file, "r") as file:
            lines = {line.rstrip() for line in file.readlines()}
            assert dependency["version"] in lines, (
                f"#{response.id}: File {dependency_version_file} does not contain"
                f" version {dependency['version']} that should have replaced the original one."
            )

    go_mod_path = path.join(bundle_dir_name, "app", "go.mod")
    assert path.exists(
        go_mod_path
    ), f"#{response.id}: File go.mod does not exist in location: {go_mod_path}"
    with open(go_mod_path, "r") as file:
        go_mod_replace = []
        for line in file:
            if line.startswith("replace "):
                go_mod_replace.append(
                    {"name": line.split()[-2], "type": "gomod", "version": line.split()[-1]}
                )
        sorted_dep_replacements = utils.make_list_of_packages_hashable(dependency_replacements)
        sorted_go_mod_replace = utils.make_list_of_packages_hashable(go_mod_replace)
        assert sorted_go_mod_replace == sorted_dep_replacements
