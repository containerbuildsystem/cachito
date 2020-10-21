# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

import utils


@pytest.mark.parametrize(
    "env_package,env_name",
    [
        ("pip_packages", "without_deps"),
        ("pip_packages", "with_deps"),
        ("gomod_packages", "without_deps"),
        ("gomod_packages", "with_deps"),
        ("gomod_packages", "vendored_with_flag"),
    ],
)
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
    env_data = utils.load_test_data(f"{env_package}.yaml")[env_name]
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    initial_response = client.create_new_request(
        payload={
            "repo": env_data["repo"],
            "ref": env_data["ref"],
            "pkg_managers": env_data["pkg_managers"],
            "flags": env_data.get("flags", []),
        },
    )
    completed_response = client.wait_for_complete_request(initial_response)
    response_data = completed_response.data
    expected_response_data = env_data["response_expectations"]
    utils.assert_elements_from_response(response_data, expected_response_data)

    client.download_and_extract_archive(completed_response.id, tmpdir)
    source_path = tmpdir.join(f"download_{str(completed_response.id)}")
    expected_files = env_data["expected_files"]
    utils.assert_expected_files(source_path, expected_files, tmpdir)

    purl = env_data["purl"]
    deps_purls = []
    source_purls = []
    if "dep_purls" in env_data:
        deps_purls = [{"purl": x} for x in env_data["dep_purls"]]
    if "source_purls" in env_data:
        source_purls = [{"purl": x} for x in env_data["source_purls"]]

    image_contents = [{"dependencies": deps_purls, "purl": purl, "sources": source_purls}]
    utils.assert_content_manifest(client, completed_response.id, image_contents)
