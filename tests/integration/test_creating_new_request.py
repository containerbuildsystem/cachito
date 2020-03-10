# SPDX-License-Identifier: GPL-3.0-or-later

import utils


def test_creating_new_request(test_env):
    """
    Send a new request to the Cachito API.

    Checks:
    * Check that response code is 201
    * Check that response contains id number, same ref and repo as in request,
        state_reason is: The request was initiated
    """
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"])
    response_created_req = client.create_new_request(
        payload={
            "repo": test_env["post"]["repo"],
            "ref": test_env["post"]["ref"],
            "pkg_managers": test_env["post"]["pkg_managers"],
        },
    )

    assert response_created_req.status == 201

    assert "id" in response_created_req.data
    assert response_created_req.id > 0
    response_specific_req = client.fetch_request(response_created_req.id)
    assert response_created_req.id == response_specific_req.id

    assert test_env["post"]["pkg_managers"] == response_created_req.data["pkg_managers"]
    assert test_env["post"]["ref"] == response_created_req.data["ref"]
    assert test_env["post"]["repo"] == response_created_req.data["repo"]
    assert test_env["post"]["ref"] == response_specific_req.data["ref"]
    assert test_env["post"]["repo"] == response_specific_req.data["repo"]

    assert response_created_req.data["state_reason"] == "The request was initiated"
