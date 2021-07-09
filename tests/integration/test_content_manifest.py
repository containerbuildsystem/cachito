# SPDX-License-Identifier: GPL-3.0-or-later

import pytest
import requests

from . import utils


def test_invalid_content_manifest_request(test_env):
    """
    Send an invalid content-manifest request to the Cachito API.

    Checks:
    * Check that the response code is 404
    """
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))

    with pytest.raises(requests.HTTPError) as e:
        client.fetch_content_manifest(request_id=0)
    assert e.value.response.status_code == 404
    assert e.value.response.json() == {"error": "The requested resource was not found"}


def test_valid_content_manifest_request(test_env, default_requests):
    """
    Send a valid content-manifest request to the Cachito API.

    Checks:
    * Check that the response code is 200
    * Check validation of the response data with content manifest JSON schema
    """
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))

    pkg_managers = test_env["content_manifest"]["pkg_managers"]
    for pkg_manager in pkg_managers:
        initial_response = default_requests[pkg_manager].initial_response
        content_manifest_response = client.fetch_content_manifest(initial_response.id)
        assert content_manifest_response.status == 200

        response_data = content_manifest_response.data
        utils.assert_content_manifest_schema(response_data)
