# SPDX-License-Identifier: GPL-3.0-or-later

import urllib
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

    def fetch_all_requests(self, page=None, per_page=None, state=None, verbose=None):
        """
        Fetch a list of requests from the Cachito API.

        :return: Object that contains response from the Cachito API
        :rtype: list
        """
        page_tuple = ("page", page)
        per_page_tuple = ("per_page", per_page)
        state_tuple = ("state", state)
        verbose_tuple = ("verbose", verbose)
        url_values = urllib.parse.urlencode(
            {
                k: v
                for k, v in [page_tuple, per_page_tuple, state_tuple, verbose_tuple]
                if v is not None
            }
        )
        result_url = f"{self._cachito_api_url}/requests?{url_values}"
        resp = requests.get(result_url)
        resp.raise_for_status()

        if all(parameter is None for parameter in [page, per_page]):
            all_items = resp.json()["items"]
            while True:
                next_page = resp.json()["meta"]["next"]
                if next_page is None:
                    break

                resp = requests.get(next_page)
                resp.raise_for_status()
                all_items += resp.json()["items"]
            return Response({"items": all_items}, None, resp.status_code)

        return Response(resp.json(), None, resp.status_code)
