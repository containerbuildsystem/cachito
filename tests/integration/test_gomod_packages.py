# SPDX-License-Identifier: GPL-3.0-or-later

import utils


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
            'The "gomod-vendor" flag must be set when your repository has vendored dependencies'
        )
        assert error_msg in completed_response.data["state_reason"]
    else:
        utils.assert_properly_completed_response(completed_response)
