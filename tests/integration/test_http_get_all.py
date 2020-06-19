# SPDX-License-Identifier: GPL-3.0-or-later

import utils


def assert_completed_requests(client: utils.Client, requests_amount):
    """Check get_all request returns expected completed values."""
    get_all_response = client.fetch_all_requests(per_page=requests_amount, page=1, state='complete')
    assert get_all_response.status == 200

    assert all(item['state'] == 'complete' for item in get_all_response.data.get('items'))
    assert 0 <= len(get_all_response.data.get('items')) <= requests_amount


def assert_no_requests_in_process_state(client: utils.Client, requests_amount, submitted_requests):
    """Check get_all request returns expected in_progress values."""
    get_all_response = client.fetch_all_requests(
        per_page=requests_amount,
        page=1,
        state='in_progress'
    )
    assert get_all_response.status == 200

    assert all(item['state'] == 'in_progress' for item in get_all_response.data.get('items'))

    response_ids = set(item.get('id') for item in get_all_response.data.get('items'))
    assert not any(request_id in response_ids for request_id in submitted_requests)
    assert 0 <= len(get_all_response.data.get('items')) <= requests_amount


def assert_verbose_correct(item_with_verbose, item_without_verbose, parameters):
    """Check get_all request with verbose=True can be translated to request with verbose=False."""
    assert item_with_verbose['id'] == item_without_verbose['id']

    expected_item_without_verbose = {}
    for key, value in item_with_verbose.items():
        if key in parameters:
            if parameters[key] == 'size':
                expected_item_without_verbose[key] = len(value)
            elif parameters[key] == 'full':
                expected_item_without_verbose[key] = value

    assert expected_item_without_verbose == item_without_verbose


def test_http_get_all(test_env, default_request, tmpdir):
    """
    Check HTTP GET_ALL works as expected.

    Process:
    * Create at least two requests and wait for them to complete (see the other tests for examples)
    * Send request to check all requests in Cachito at the /api/v1/requests API endpoint
    * Send requests to check all requests with filtering parameters

    Checks:
    * Check that response code is 200
    * Check that filtering parameters are working properly (page, per_page, state, and verbose)
    """
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"])
    requests_amount = test_env["http_get_all"]["requests_amount"]
    submitted_requests = []
    for _ in range(requests_amount):
        initial_response = client.create_new_request(
            payload={
                "repo": test_env["package"]["repo"],
                "ref": test_env["package"]["ref"],
                "pkg_managers": test_env["package"]["pkg_managers"],
            },
        )
        completed_response = client.wait_for_complete_request(initial_response)
        response_data = completed_response.data
        submitted_requests.append(response_data.get('id'))

    assert_completed_requests(client, requests_amount)
    assert_no_requests_in_process_state(client, requests_amount, submitted_requests)

    get_all_response_without_verbose = client.fetch_all_requests(per_page=1, page=1)
    item_without_verbose = get_all_response_without_verbose.data.get('items')[0]
    get_all_response_with_verbose = client.fetch_all_requests(per_page=1, page=1, verbose=True)
    item_with_verbose = get_all_response_with_verbose.data.get('items')[0]
    params_without_verbose = test_env['http_get_all']['params_verbose_false']
    assert_verbose_correct(item_with_verbose, item_without_verbose, params_without_verbose)
