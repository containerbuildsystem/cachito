# SPDX-License-Identifier: GPL-3.0-or-later

import utils


def test_http_get_all(test_env):
    """
    Check the API endpoint to get all requests.

    Process:
    * Pre-populate data in Cachito by submitting basic requests and waiting for them to complete.
    * Verify the submitted requests are not incorrectly marked as "in_progress".
    * Verify the submitted requests can be found by using the state=complete filter.
    Checks:
    * Check that response code is 200
    * Check that filtering parameters are working properly (page, per_page, and state)
    """
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    requests_amount = test_env["http_get_all"]["requests_amount"]
    submitted_requests = []
    payload = {
        "repo": test_env["packages"]["gomod"]["repo"],
        "ref": test_env["packages"]["gomod"]["ref"],
        "pkg_managers": test_env["packages"]["gomod"]["pkg_managers"],
    }
    initial_responses = [client.create_new_request(payload) for _ in range(requests_amount)]
    for initial_response in initial_responses:
        completed_response = client.wait_for_complete_request(initial_response)
        response_data = completed_response.data
        submitted_requests.append(response_data["id"])

    assert_no_requests_in_progress_state(client, requests_amount, submitted_requests)
    assert_completed_requests(client, submitted_requests)


def test_get_all_verbose(test_env):
    """
    Check the API endpoint to get all requests with the verbose flag enabled.

    Process:
    * Create a request and wait for it to complete
    * Send request get_all to the Cachito API with verbose
    Checks:
    * Check that response code is 200
    * Check that verbose is working properly
    """
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    initial_response = client.create_new_request(
        payload={
            "repo": test_env["packages"]["gomod"]["repo"],
            "ref": test_env["packages"]["gomod"]["ref"],
            "pkg_managers": test_env["packages"]["gomod"]["pkg_managers"],
        },
    )
    client.wait_for_complete_request(initial_response)

    query_params = {"per_page": 1, "page": 1, "state": "complete", "verbose": True}
    request_id = initial_response.id
    found_request = False
    while not found_request:
        response = client.fetch_all_requests(query_params, all_pages=False)
        assert response.status == 200
        if response.data["items"][0]["id"] == request_id:
            found_request = True
        query_params["page"] += 1

    expected_request_data = client.fetch_request(request_id).data
    assert response.data["items"][0] == expected_request_data


def assert_no_requests_in_progress_state(client: utils.Client, requests_amount, submitted_requests):
    """Check get_all request returns expected in_progress values."""
    query_params = {"per_page": requests_amount, "page": 1, "state": "in_progress"}
    response = client.fetch_all_requests(query_params, all_pages=False)
    assert response.status == 200

    assert all(
        item["state"] == "in_progress" for item in response.data["items"]
    ), "At least one of requests was not in in_progress state"

    response_ids = set(item["id"] for item in response.data["items"])
    # Check there are no completed requests in the response.
    assert not any(request_id in response_ids for request_id in submitted_requests), (
        f"At least one of requests {response_ids} was in completed state. ",
        "All requests are expected to be in other state than completed.",
    )
    assert 0 <= len(response.data["items"]) <= requests_amount, (
        f"Number of obtained responses ({len(response.data['items'])}) "
        f"is not in the range from 0 to {requests_amount}"
    )


def assert_completed_requests(client: utils.Client, submitted_ids):
    """Check get_all request returns submitted requests in complete state."""
    query_params = {"per_page": len(submitted_ids), "page": 1, "state": "complete"}
    submitted_ids = submitted_ids.copy()
    while len(submitted_ids):
        response = client.fetch_all_requests(query_params, all_pages=False)
        assert response.status == 200
        assert response.data["items"], "Cachito did not return any requests"

        for response_item in response.data["items"]:
            assert response_item["state"] == "complete"
            if response_item["id"] in submitted_ids:
                submitted_ids.remove(response_item["id"])

        query_params["page"] += 1

    assert len(submitted_ids) == 0
