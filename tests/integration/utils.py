# SPDX-License-Identifier: GPL-3.0-or-later

from collections import namedtuple
import os
import time

import jsonschema
import requests
from requests_kerberos import HTTPKerberosAuth


Response = namedtuple("Response", "data id status")


class Client:
    """Simplifies access to the Cachito API."""

    def __init__(self, cachito_api_url, cachito_api_auth_type, timeout=15):
        """
        Initialize the Client class.

        :attribute str _cachito_api_url: URL of the Cachito API
        :attribute _cachito_api_auth_type: kind of authentication used
        :attribute int _timeout: timeout for completing request
        """
        self._cachito_api_url = cachito_api_url
        self._cachito_api_auth_type = cachito_api_auth_type
        self._timeout = timeout

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
        resp = requests.post(
            f"{self._cachito_api_url}/requests",
            headers={"Content-Type": "application/json"},
            json=payload,
            **self._get_authentication_params(),
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
        :raises TimeoutError: if the request would not complete in time
        """
        start_time = time.time()
        timeout_secs = self._timeout * 60
        while response.data["state"] == "in_progress":
            if time.time() - start_time >= timeout_secs:
                raise TimeoutError(
                    f"The Cachito request did not complete within {self._timeout} minutes"
                )

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

    def _get_authentication_params(self):
        """
        Return the parameters required to authenticate with Cachito.

        :return: keyword parameters to be used with requests module
        :rtype: dict
        """
        if self._cachito_api_auth_type == "cert":
            return {"cert": (os.getenv("CACHITO_TEST_CERT"), os.getenv("CACHITO_TEST_KEY"))}
        elif self._cachito_api_auth_type == "kerberos":
            return {"auth": HTTPKerberosAuth()}
        return {"auth": None}


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


def make_list_of_packages_hashable(data):
    """
    Convert and sort the list of dicts to a list of lists from the keys name, type, and version.

    :param data: list of dictionaries containing keys name, type and version
    :return: list of lists with values name, type and version in this order
    """
    return sorted([[i["name"], i["type"], i["version"]] for i in data])


def assert_content_manifest_schema(response_data):
    """Validate content manifest according with JSON schema."""
    icm_spec = response_data["metadata"]["icm_spec"]
    schema = requests.get(icm_spec, timeout=30).json()
    assert validate_json(schema, response_data)


def assert_packages_from_response(response_data, expected_packages):
    """
    Check amount and params of packages in the response data.

    :param dict response_data: response data from the Cachito request
    :param list expected_packages: expected params of packages
    """
    packages = response_data["packages"]
    assert len(packages) == len(expected_packages)
    for expected_pkg in expected_packages:
        assert expected_pkg in packages


def assert_expected_files(source_path, expected_file_urls=None, check_content=True):
    """
    Check that the source path includes expected files.

    :param str source_path: local path for checking
    :param dict expected_file_urls: {"relative_path/file_name": "URL", ...}
    :param bool check_content: The flag to check content of files
    """
    if expected_file_urls is None:
        expected_file_urls = {}
    assert os.path.exists(source_path) and os.path.isdir(source_path)
    files = []
    # Go through all files in source_code_path and it's subdirectories
    for root, _, source_files in os.walk(source_path):
        for file_name in source_files:
            # Get path to file in the project
            absolute_file_path = os.path.join(root, file_name)
            relative_file_path = os.path.relpath(absolute_file_path, start=source_path)
            # Assert that content of source file is equal to expected
            with open(absolute_file_path, "rb") as f:
                if check_content:
                    # Download expected file
                    file_url = expected_file_urls[relative_file_path]
                    expected_file = requests.get(file_url).content
                    assert f.read() == expected_file
                else:
                    assert f.read()
            files.append(relative_file_path)

    # Assert that there are no missing or extra files
    assert set(files) == set(list(expected_file_urls))


def assert_content_manifest(client, request_id, image_contents):
    """
    Check that the content manifest is successfully generated and contains correct content.

    Checks:
    * Check that status of content-manifest request is 200
    * Validate content manifest schema
    * Check image_contents from content-manifest

    :param Client client: the Cachito API client
    :param int request_id: The Cachito request id
    :param list image_contents: expected image content part from content manifest
    """
    content_manifest_response = client.fetch_content_manifest(request_id)
    assert content_manifest_response.status == 200

    response_data = content_manifest_response.data
    assert_content_manifest_schema(response_data)
    assert image_contents == content_manifest_response.data["image_contents"]
