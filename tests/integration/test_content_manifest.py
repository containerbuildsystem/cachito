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
