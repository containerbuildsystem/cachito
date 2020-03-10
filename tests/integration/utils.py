# SPDX-License-Identifier: GPL-3.0-or-later

from collections import namedtuple

import requests
from requests_kerberos import HTTPKerberosAuth


Response = namedtuple("Response", "data id status")


class Client:
    """Simplifies access to the Cachito API."""

    def __init__(self, cachito_api_url, cachito_api_auth_type):
        """
        Initialize the Client class.

        :attribute str _cachito_api_url: URL of the Cachito API
        :attribute _cachito_api_auth_type: kind of authentication used
        """
        self._cachito_api_url = cachito_api_url
        self._cachito_api_auth_type = cachito_api_auth_type

    def fetch_request(self, request_id):
        """
        Fetch a request from the Cachito API.

        :param int request_id: ID of the request in Cachito
        :return: Object that contains response from the Cachito API
        :rtype: Response
        :raises requests.exceptions.HTTPError: if the request to the Cachito API fails
        """
        resp = requests.get(f"{self._cachito_api_url}/requests/{request_id}")
        resp.raise_for_status()
        return Response(resp.json(), resp.json()["id"], resp.status_code)

    def create_new_request(self, payload):
        """
        Create a new request in Cachito.

        :param dict payload: Payload for request
        :return: Object that contains response from the Cachito API
        :rtype: Response
        :raises requests.exceptions.HTTPError: if the request to the Cachito API fails
        """
        authentication_mapping = {"kerberos": HTTPKerberosAuth()}
        resp = requests.post(
            f"{self._cachito_api_url}/requests",
            auth=authentication_mapping.get(self._cachito_api_auth_type),
            headers={"Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        return Response(resp.json(), resp.json()["id"], resp.status_code)
