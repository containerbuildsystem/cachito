# SPDX-License-Identifier: GPL-3.0-or-later

from . import utils


def test_complete_request_no_error_info(test_env):
    """
    Check that the response of a complete request does not include error information.

    Process:
    Send new request to the Cachito API
    Send request to check status of existing request

    Checks:
    * Check that the request error origin and type are not present in the response data
    """
    env_data = utils.load_test_data("pip_packages.yaml")["local_path"]
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    initial_response = client.create_new_request(
        payload={"repo": env_data["repo"], "ref": env_data["ref"]}
    )
    completed_response = client.wait_for_complete_request(initial_response)
    assert completed_response.status == 200
    assert completed_response.data["state"] == "complete"
    assert (
        "error_origin" not in completed_response.data
        and "error_type" not in completed_response.data
    )


def test_failed_request_error_info(test_env):
    """
    Check that the response of a failed request includes appropriate error information.

    Process:
    Send new request to the Cachito API
    Send request to check status of existing request

    Checks:
    * Validate the request error origin and type in the response data
    """
    env_data = utils.load_test_data("pip_packages.yaml")["local_path"]
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    initial_response = client.create_new_request(
        payload={
            "repo": env_data["repo"],
            "ref": env_data["ref"],
            "pkg_managers": ["npm"],  # Wrong package manager
        }
    )
    completed_response = client.wait_for_complete_request(initial_response)
    assert completed_response.status == 200
    assert completed_response.data["state"] == "failed"
    assert completed_response.data["state_reason"] == (
        "The npm-shrinkwrap.json or package-lock.json file "
        "must be present for the npm package manager"
    )
    assert "error_origin" in completed_response.data and "error_type" in completed_response.data
    assert completed_response.data["error_origin"] == "client"
    assert completed_response.data["error_type"] == "InvalidRepoStructure"
