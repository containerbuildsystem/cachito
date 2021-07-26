# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from . import utils


def test_gomod_vendor_without_flag(test_env):
    """
    Validate failing of gomod vendor request without flag.

    Checks:
    * The request failed with expected error message
    """
    env_data = utils.load_test_data("gomod_packages.yaml")["vendored_without_flag"]
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    initial_response = client.create_new_request(
        payload={
            "repo": env_data["repo"],
            "ref": env_data["ref"],
            "pkg_managers": env_data["pkg_managers"],
        },
    )
    completed_response = client.wait_for_complete_request(initial_response)
    if test_env.get("strict_mode_enabled"):
        assert completed_response.status == 200
        assert completed_response.data["state"] == "failed"
        error_msg = (
            'The "gomod-vendor" or "gomod-vendor-check" flag must be set when your repository has '
            "vendored dependencies."
        )
        assert error_msg in completed_response.data["state_reason"], (
            f"#{completed_response.id}: Request failed correctly, but with unexpected message: "
            f"{completed_response.data['state_reason']}. Expected message was: {error_msg}"
        )
    else:
        utils.assert_properly_completed_response(completed_response)


@pytest.mark.parametrize("env_name", [("wrong_vendor"), ("empty_vendor")])
def test_gomod_vendor_check_fail(env_name, test_env):
    """
    Validate failing of gomod vendor request with gomod-vendor-check flag and inconsistent vendor.

    Checks:
    * The request fails with expected error message
    """
    env_data = utils.load_test_data("gomod_vendor_check.yaml")[env_name]
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    initial_response = client.create_new_request(
        payload={
            "repo": env_data["repo"],
            "ref": env_data["ref"],
            "flags": env_data["flags"],
            "pkg_managers": env_data["pkg_managers"],
        },
    )
    completed_response = client.wait_for_complete_request(initial_response)
    assert completed_response.status == 200
    assert completed_response.data["state"] == "failed"
    error_msg = (
        "The content of the vendor directory is not consistent with go.mod. "
        "Run `go mod vendor` locally to fix this problem. See the logs for more details."
    )
    assert error_msg in completed_response.data["state_reason"], (
        f"#{completed_response.id}: Request failed correctly, but with unexpected message: "
        f"{completed_response.data['state_reason']}. Expected message was: {error_msg}"
    )
