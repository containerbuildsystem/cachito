# SPDX-License-Identifier: GPL-3.0-or-later

from . import utils


def test_failing_pip_local_path(test_env):
    """
    Validate failing of the pip package request with local dependencies.

    Process:
    Send new request to the Cachito API
    Send request to check status of existing request

    Checks:
    * Check that the request fails with expected error
    """
    env_data = utils.load_test_data("pip_packages.yaml")["local_path"]
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    initial_response = client.create_new_request(
        payload={"repo": env_data["repo"], "ref": env_data["ref"], "pkg_managers": ["pip"]}
    )
    completed_response = client.wait_for_complete_request(initial_response)
    assert completed_response.status == 200
    assert completed_response.data["state"] == "failed"
    error_msg = "Direct references with 'file' scheme are not supported"
    assert error_msg in completed_response.data["state_reason"], (
        f"#{completed_response.id}: Request failed correctly, but with unexpected message: "
        f"{completed_response.data['state_reason']}. Expected message was: {error_msg}"
    )


def test_failing_pip_invalid_req_path(test_env):
    """
    Validate failing of the pip package request with a non-existent requirement file path.

    Process:
    Send new request to the Cachito API
    Send request to check status of existing request

    Checks:
    * Check that the request fails with expected error
    """
    env_data = utils.load_test_data("pip_packages.yaml")["local_path"]
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    invalid_path = "foo.txt"
    initial_response = client.create_new_request(
        payload={
            "repo": env_data["repo"],
            "ref": env_data["ref"],
            "pkg_managers": ["pip"],
            "packages": {"pip": [{"requirements_files": [invalid_path]}]},
        }
    )
    completed_response = client.wait_for_complete_request(initial_response)
    assert completed_response.status == 200
    assert completed_response.data["state"] == "failed"
    error_msg = "Following requirement file has an invalid path: "
    assert all(msg in completed_response.data["state_reason"] for msg in [error_msg, invalid_path])
