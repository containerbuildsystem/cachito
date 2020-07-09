# SPDX-License-Identifier: GPL-3.0-or-later

import pytest
import requests

import utils


def test_invalid_content_manifest_request(test_env):
    """
    Send an invalid content-manifest request to the Cachito API.

    Checks:
    * Check that the response code is 404
    """
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"])

    with pytest.raises(requests.HTTPError) as e:
        client.fetch_content_manifest(request_id=0)
    assert e.value.response.status_code == 404
    assert e.value.response.json() == {"error": "The requested resource was not found"}


def test_valid_content_manifest_request(test_env, default_request):
    """
    Send a valid content-manifest request to the Cachito API.

    Checks:
    * Check that the response code is 200
    * Check validation of the response data with content manifest JSON schema
    """
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"])

    initial_response = default_request.initial_response
    content_manifest_response = client.fetch_content_manifest(initial_response.id)
    assert content_manifest_response.status == 200

    response_data = content_manifest_response.data
    assert_content_manifest_schema(response_data)


def assert_content_manifest_schema(response_data):
    """Validate content manifest according with JSON schema."""
    icm_spec = response_data["metadata"]["icm_spec"]
    schema = requests.get(icm_spec, timeout=30).json()
    assert utils.validate_json(schema, response_data)
