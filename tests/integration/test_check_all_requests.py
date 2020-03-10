# SPDX-License-Identifier: GPL-3.0-or-later

import utils


def test_check_all_requests(test_env):
    """
    Verify that the filtering parameters are working properly.

    Process:
    * Send request to check all requests in the Cachito API
    * Send requests to check all requests with filtering parameters

    Checks:
    * Check that response code is 200
    * Check that filtering parameters are working properly (page, per_page, state and verbose)
    """
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"])
    resp = client.fetch_all_requests()
    assert resp.status == 200

    resp = client.fetch_all_requests(page=test_env["get_all"]["page"])
    assert resp.data["meta"]["page"] == test_env["get_all"]["page"]

    resp = client.fetch_all_requests(per_page=test_env["get_all"]["per_page"])
    assert resp.data["meta"]["per_page"] == test_env["get_all"]["per_page"]

    resp = client.fetch_all_requests(state=test_env["get_all"]["state"])
    response_states = [item["state"] for item in resp.data["items"]]
    assert all(state == test_env["get_all"]["state"] for state in response_states)

    resp_verbose_true = client.fetch_all_requests(verbose=True)
    resp_verbose_false = client.fetch_all_requests(verbose=False)
    assert len(resp_verbose_true.data["items"][0]) > len(resp_verbose_false.data["items"][0])
