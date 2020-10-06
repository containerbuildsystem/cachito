# SPDX-License-Identifier: GPL-3.0-or-later

import utils


def test_valid_data_in_request(test_env, default_requests):
    """
    Validate data in the request.

    Process:
    Send new request to the Cachito API
    Send request to check status of existing request

    Checks:
    * Check that response code is 200
    * Check in the response that state is complete
    * Check that "packages" and "dependencies" keys have appropriate values
    """
    response = default_requests["gomod"].complete_response
    assert response.status == 200
    assert response.data["state"] == "complete"

    response_dependencies = utils.make_list_of_packages_hashable(response.data["dependencies"])
    expected_dependencies = test_env["get"]["gomod"]["dependencies"]
    assert response_dependencies == sorted(expected_dependencies)

    response_packages = utils.make_list_of_packages_hashable(response.data["packages"])
    expected_packages = test_env["get"]["gomod"]["packages"]
    assert response_packages == sorted(expected_packages)


def test_npm_basic(test_env, default_requests):
    """
    A basic integration test for the npm package manager.

    Process:
    * Send new request to the Cachito API
    * Send request to check status of existing request

    Checks:
    * Verify that the request completes successfully
    * Verify that there is a correct package entry
    * Verify that there is a correct number of dependencies
    * Verify that the tslib dependency is not a dev dependency (dev key in the dependencies array)
    * Verify that the other dependencies are dev (dev key in the dependencies array)
    * Verify that the environment variables "CHROMEDRIVER_SKIP_DOWNLOAD": "true",
        "CYPRESS_INSTALL_BINARY": "0", "GECKODRIVER_SKIP_DOWNLOAD": "true",
        and "SKIP_SASS_BINARY_DOWNLOAD_FOR_CI": "true" are set.
    """
    response = default_requests["npm"].complete_response
    utils.assert_properly_completed_response(response)

    response_packages = utils.make_list_of_packages_hashable(response.data["packages"])
    expected_packages = test_env["get"]["npm"]["packages"]
    assert response_packages == expected_packages

    assert len(response.data["dependencies"]) == test_env["get"]["npm"]["dependencies_count"]

    for item in response.data["dependencies"]:
        if item["name"] not in test_env["get"]["npm"]["non_dev_dependencies"]:
            assert item["dev"]
        else:
            assert not item["dev"]

    env_variables = response.data["environment_variables"]
    assert env_variables["CHROMEDRIVER_SKIP_DOWNLOAD"] == "true"
    assert env_variables["CYPRESS_INSTALL_BINARY"] == "0"
    assert env_variables["GECKODRIVER_SKIP_DOWNLOAD"] == "true"
    assert env_variables["SKIP_SASS_BINARY_DOWNLOAD_FOR_CI"] == "true"


def test_various_packages(test_env):
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    for pkg_manager, package in test_env["various_packages"].items():
        initial_response = client.create_new_request(
            payload={
                "repo": package["repo"],
                "ref": package["ref"],
                "pkg_managers": [pkg_manager],
            },
        )
        completed_response = client.wait_for_complete_request(initial_response)
        utils.assert_properly_completed_response(completed_response)

        assert len(completed_response.data["dependencies"]) == package["dependencies_count"]
