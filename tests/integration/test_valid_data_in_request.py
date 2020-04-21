# SPDX-License-Identifier: GPL-3.0-or-later

import operator

import utils


def test_valid_data_in_request(test_env):
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
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"])
    response_created_req = client.create_new_request(
        payload={
            "repo": test_env["get"]["repo"],
            "ref": test_env["get"]["ref"],
            "pkg_managers": test_env["get"]["pkg_managers"],
        },
    )

    response = client.fetch_request(response_created_req.id)
    assert response.status == 200

    response = client.wait_for_complete_request(response)
    assert response.data["state"] == "complete"

    response_dependencies = list_of_dict_to_list_of_name_type_version(response.data["dependencies"])
    expected_dependencies = test_env["get"]["dependencies"]
    assert response_dependencies == expected_dependencies

    response_packages = list_of_dict_to_list_of_name_type_version(response.data["packages"])
    expected_packages = test_env["get"]["packages"]
    assert response_packages == expected_packages


def list_of_dict_to_list_of_name_type_version(data):
    """
    Convert the list of dictionaries to a list of lists from the keys name, type, and version.

    :param data: list of dictionaries containing keys name, type and version
    :return: list of lists with values name, type and version in this order
    """
    sorted_packages = sorted(data, key=operator.itemgetter("name"))

    return [[i["name"], i["type"], i["version"]] for i in sorted_packages]
