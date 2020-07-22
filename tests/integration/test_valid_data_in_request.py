# SPDX-License-Identifier: GPL-3.0-or-later

from utils import make_list_of_packages_hashable


def test_valid_data_in_request(test_env, default_request):
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
    response = default_request.complete_response
    assert response.status == 200
    assert response.data["state"] == "complete"

    response_dependencies = make_list_of_packages_hashable(response.data["dependencies"])
    expected_dependencies = test_env["get"]["dependencies"]
    assert response_dependencies == sorted(expected_dependencies)

    response_packages = make_list_of_packages_hashable(response.data["packages"])
    expected_packages = test_env["get"]["packages"]
    assert response_packages == sorted(expected_packages)
