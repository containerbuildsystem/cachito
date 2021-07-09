# SPDX-License-Identifier: GPL-3.0-or-later

from . import utils


def test_creating_new_request(test_env, default_requests):
    """
    Send a new request to the Cachito API.

    Checks:
    * Check that response code is 201
    * Check that response contains id number, same ref and repo as in request,
        state_reason is: The request was initiated
    """
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    response_created_req = default_requests["gomod"].initial_response
    assert response_created_req.status == 201

    assert "id" in response_created_req.data
    assert response_created_req.id > 0

    response_specific_req = client.fetch_request(response_created_req.id)
    assert response_created_req.id == response_specific_req.id

    response_pkg_managers = set(response_created_req.data["pkg_managers"])
    assert set(test_env["packages"]["gomod"]["pkg_managers"]) == response_pkg_managers
    assert test_env["packages"]["gomod"]["ref"] == response_created_req.data["ref"]
    assert test_env["packages"]["gomod"]["repo"] == response_created_req.data["repo"]
    assert test_env["packages"]["gomod"]["ref"] == response_specific_req.data["ref"]
    assert test_env["packages"]["gomod"]["repo"] == response_specific_req.data["repo"]

    assert response_created_req.data["state_reason"] == "The request was initiated"
