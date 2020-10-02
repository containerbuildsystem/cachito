# SPDX-License-Identifier: GPL-3.0-or-later

from os import path
import pytest

import utils


@pytest.mark.parametrize("env_name", ["without_deps", "with_deps"])
@pytest.mark.parametrize("env_package", ["pip_packages", "gomod_packages"])
def test_packages(env_package, env_name, test_env, tmpdir):
    """
    Validate data in the package request according to pytest env_name and env_package parameter.

    Process:
    Send new request to the Cachito API
    Send request to check status of existing request

    Checks:
    * Check that the request completes successfully
    * Check that expected packages are identified in response
    * Check that expected dependencies are identified in response
    * Check response parameters of the package
    * Check that the source tarball includes the application source code
    * Check that the source tarball includes expected deps directory
    * Check: The content manifest is successfully generated and contains correct content
    """
    env_data = test_env[env_package][env_name]
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    initial_response = client.create_new_request(
        payload={
            "repo": env_data["repo"],
            "ref": env_data["ref"],
            "pkg_managers": env_data["pkg_managers"],
        },
    )
    completed_response = client.wait_for_complete_request(initial_response)
    response_data = completed_response.data
    utils.sort_pkgs_and_deps_in_place(response_data["packages"], response_data["dependencies"])

    utils.assert_properly_completed_response(completed_response)

    expected_package_params = env_data["packages"]
    expected_deps = env_data["dependencies"]
    utils.sort_pkgs_and_deps_in_place(expected_package_params, expected_deps)
    utils.assert_element_from_response(response_data, expected_package_params, "packages")
    utils.assert_element_from_response(response_data, expected_deps, "dependencies")

    client.download_and_extract_archive(completed_response.id, tmpdir)

    source_name = tmpdir.join(f"download_{str(completed_response.id)}")
    expected_file_urls = env_data["expected_files"]
    # Check that the source tarball includes the application source code under the app directory.
    utils.assert_expected_files(path.join(source_name, "app"), expected_file_urls)

    expected_deps_file_urls = env_data["expected_deps_files"]
    # Check that the source tarball includes an expected files in the deps directory.
    utils.assert_expected_files(
        path.join(source_name, "deps"), expected_deps_file_urls, check_content=False
    )
    purl = env_data["purl"]
    if "dep_purls" in env_data:
        deps_purls = [{"purl": x} for x in env_data["dep_purls"]]
    else:
        deps_purls = []
    image_contents = [{"dependencies": deps_purls, "purl": purl, "sources": deps_purls}]
    utils.assert_content_manifest(client, completed_response.id, image_contents)
