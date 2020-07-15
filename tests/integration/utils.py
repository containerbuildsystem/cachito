# SPDX-License-Identifier: GPL-3.0-or-later

from collections import namedtuple
import time

import jsonschema
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

    def download_bundle(self, request_id, file_name_tar):
        """
        Download a bundle archive.

        :param str file_name_tar: Name of the downloaded bundle
        :param int request_id:  ID of the request in Cachito
        :return: Object that contains response from the Cachito API
        :rtype: Response
        :raises requests.exceptions.HTTPError: if the request to the Cachito API fails
        """
        download_url = f"{self._cachito_api_url}/requests/{request_id}/download"
        with requests.get(download_url, stream=True) as resp:
            resp.raise_for_status()
            with open(file_name_tar, "wb") as file:
                for chunk in resp.iter_content(chunk_size=8192):
                    file.write(chunk)

        return Response(None, request_id, resp.status_code)

    def wait_for_complete_request(self, response):
        """
        Wait for a request to complete fetching the application source and dependencies.

        :param Response response: Object that contains response from the Cachito API
        :return: Object that contains response from the Cachito API
        :rtype: Response
        :raises TimeoutError: if the request would not complete within 5 minutes
        """
        start_time = time.time()
        while response.data["state"] == "in_progress":
            # 300 seconds
            if time.time() - start_time >= 300:
                raise TimeoutError("The Cachito request did not complete within 5 minutes")
            time.sleep(5)
            response = self.fetch_request(response.id)

        return response

    def fetch_all_requests(self, query_params=None, all_pages=True):
        """
        Fetch a list of requests from the Cachito API.

        :param dict query_params: Request parameters and values (page, per_page, status, verbose)
        :param bool all_pages: Flag to get all pages from the Cachito API
        :return: Object that contains response from the Cachito API
        :rtype: list
        """
        if not query_params:
            query_params = {}
        request_url = f"{self._cachito_api_url}/requests"
        all_items = []
        while request_url:
            resp = requests.get(request_url, params=query_params, timeout=15)
            resp.raise_for_status()
            all_items += resp.json()["items"]
            if not all_pages:
                break
            request_url = resp.json()["meta"]["next"]

        return Response({"items": all_items}, None, resp.status_code)

    def fetch_content_manifest(self, request_id):
        """
        Fetch a contest manifest by request_id from the Cachito API.

        :param int request_id: The ID of the Cachito request
        :return: An object that contains the response from the Cachito API
        :rtype: Response
        """
        resp = requests.get(f"{self._cachito_api_url}/requests/{request_id}/content-manifest")
        resp.raise_for_status()
        return Response(resp.json(), request_id, resp.status_code)


def escape_path_go(dependency):
    """
    Escape uppercase characters in names of Golang packages.

    Replacing every uppercase letter with an exclamation mark followed by the lowercase letter.
    This is described in:
    https://github.com/golang/mod/blob/2addee1ccfb22349ab47953a3046338e461eb4d1/module/module.go#L46

    :param str dependency: Name of the dependency
    :return: Escaped dependency name
    :rtype: str
    """
    if not dependency.islower():
        package_name = ""
        for char in dependency:
            if char.isupper():
                char = "!" + char.lower()
            package_name += char
        return package_name
    else:
        return dependency


def validate_json(json_schema, json_data):
    """
    Validate JSON data according to JSON schema.

    :param str json_schema: Expected JSON schema for validation
    :param str json_data: Data to be validated
    :rtype: bool
    """
    try:
        jsonschema.validate(instance=json_data, schema=json_schema)
    except jsonschema.exceptions.ValidationError:
        return False
    return True
