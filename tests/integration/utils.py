# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
import os
import shutil
import time
from collections import namedtuple

import jsonschema
import requests
import yaml
from requests.packages.urllib3.util.retry import Retry
from requests_kerberos import HTTPKerberosAuth

from tests.helper_utils import assert_directories_equal

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
        self.requests_session = get_requests_session()

    def fetch_request(self, request_id):
        """
        Fetch a request from the Cachito API.

        :param int request_id: ID of the request in Cachito
        :return: Object that contains response from the Cachito API
        :rtype: Response
        :raises requests.exceptions.HTTPError: if the request to the Cachito API fails
        """
        resp = self.requests_session.get(f"{self._cachito_api_url}/requests/{request_id}")
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
        resp = self.requests_session.post(
            f"{self._cachito_api_url}/requests",
            headers={"Content-Type": "application/json"},
            json=payload,
            **self._get_authentication_params(),
        )
        resp.raise_for_status()
        return Response(resp.json(), resp.json()["id"], resp.status_code)

    def download_and_extract_archive(self, request_id, tmpdir):
        """
        Download a bundle archive and extract it.

        :param int request_id: ID of the request in Cachito
        :param tmpdir: archive is extracted to this temporary directory
        """
        source_name = os.path.join(tmpdir, f"download_{str(request_id)}")
        file_name_tar = os.path.join(tmpdir, f"download_{str(request_id)}.tar.gz")
        download_url = f"{self._cachito_api_url}/requests/{request_id}/download"
        download_archive(download_url, file_name_tar)
        shutil.unpack_archive(file_name_tar, source_name)

    def wait_for_complete_request(self, response: Response):
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
            resp = self.requests_session.get(request_url, params=query_params, timeout=15)
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
        resp = self.requests_session.get(
            f"{self._cachito_api_url}/requests/{request_id}/content-manifest"
        )
        resp.raise_for_status()
        return Response(resp.json(), request_id, resp.status_code)

    def fetch_request_metrics(self, **params) -> requests.Response:
        resp = self.requests_session.get(f"{self._cachito_api_url}/request-metrics", params=params)
        resp.raise_for_status()
        return resp

    def fetch_request_metrics_summary(self, **params) -> requests.Response:
        resp = self.requests_session.get(
            f"{self._cachito_api_url}/request-metrics/summary", params=params,
        )
        resp.raise_for_status()
        return resp

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


def download_archive(download_url, archive_path):
    """
    Download an archive.

    :param download_url: URL to get the archive
    :param archive_path: Path to the downloaded bundle
    """
    requests_session = get_requests_session()
    with requests_session.get(download_url, stream=True) as resp:
        resp.raise_for_status()
        with open(archive_path, "wb") as file:
            for chunk in resp.iter_content(chunk_size=8192):
                file.write(chunk)


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


def get_requests_session():
    """
    Get a request session with a retry option.

    :return: the requests session
    :rtype: requests.Session
    """
    session = requests.Session()
    retry = Retry(
        total=5, read=5, connect=5, backoff_factor=1.3, status_forcelist=(500, 502, 503, 504)
    )
    adapter = requests.adapters.HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def get_sha256_hash_from_file(filename):
    """
    Return sha256 hash of file.

    :param str filename: The path to file
    :return: sha256 hash of file
    :rtype: str
    """
    # make a hash object
    h = hashlib.sha256()

    # open file for reading in binary mode
    with open(filename, "rb") as file:
        # loop till the end of the file, 1024 bytes at a time
        chunk = file.read(1024)
        while chunk:
            h.update(chunk)
            chunk = file.read(1024)

    # return the hex representation of digest
    return h.hexdigest()


def load_test_data(file_name):
    """
    Load the test configuration.

    :param str file_name: File with test data
    :return: Test configuration for file_name.
    :rtype:  dict
    """
    test_data_dir = os.getenv("CACHITO_TEST_DATA", "tests/integration/test_data")
    test_data_file = os.path.join(test_data_dir, file_name)
    assert os.path.isfile(
        test_data_file
    ), f"File {file_name} does not exist in path: {test_data_file}"
    with open(test_data_file) as f:
        test_data = yaml.safe_load(f)
    return test_data


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
    requests_session = get_requests_session()
    schema = requests_session.get(icm_spec, timeout=30).json()
    assert validate_json(
        schema, response_data
    ), f"ICM data not valid for schema at {response_data['metadata']['icm_spec']}: {response_data}"


def assert_elements_from_response(response_data, expected_response_data):
    """
    Check elements from the response data.

    In case the expected element is a list, every element in the list will be checked
    (otherwise only equality between expected_element and element from response).
    Elements "packages" and "dependencies" will be sorted for
    response_data and expected_response_data.

    :param dict response_data: response data from the Cachito request
    :param expected_response_data: expected content of particular elements in response:
        {<element_name> : <expected_data>}
    """
    for element_name, expected_data in expected_response_data.items():
        assert response_data[element_name] == expected_data, (
            f"#{response_data['id']}: elements in reponse differs from test expactations. \n"
            f"Response elements: "
            f"{json.dumps(response_data[element_name], indent=4, sort_keys=True)}, \n"
            f"Test expectations: {json.dumps(expected_data, indent=4, sort_keys=True)}"
        )


def assert_expected_files(source_path, expected_files, tmpdir):
    """
    Check that the source path includes expected files in directories.

    Stages for not empty directory checks:
    1. If we check `deps` directory, extract package in `deps`
    2. Download and extract expected package from expected_files
    3. Compare files recursively with expected ones
    4. Delete downloaded and extracted temporary data

    :param str source_path: local path for checking
    :param dict expected_files: Dict with expected file data:
        {<directory_name>: <archive_URL>}
    :param tmpdir: Testing function for providing temporary directory
    """
    for dir_to_check, archive_url in expected_files.items():
        # A directory path to check
        test_path = os.path.join(source_path, dir_to_check)
        # If there is no link to expected archive, the directory should be empty
        if not archive_url:
            assert (
                len(os.listdir(test_path)) == 0
            ), f"Directory: {test_path} not found or not empty as expected."
        else:
            dir_identifier = dir_to_check.replace("/", "_")
            # A directory path with extracted deps
            deps_data_path = os.path.join(tmpdir, f"test_source_{dir_identifier}")
            # An archive path with expected data
            expected_archive = os.path.join(tmpdir, f"archive_{dir_identifier}.tar.gz")
            # A directory path with extracted expected data
            expected_data_path = os.path.join(tmpdir, f"expected_data_{dir_identifier}")
            # Dependencies instead of source code are saved as archives.
            # Therefore we should extract it firstly
            if os.path.isdir(test_path):
                package_root_dir = test_path
            else:
                archive_path = test_path
                shutil.unpack_archive(archive_path, deps_data_path)
                # deps_data_path is unique and contains only one expected package
                package_root_dir = os.path.join(deps_data_path, os.listdir(deps_data_path)[0])
            download_archive(archive_url, expected_archive)
            shutil.unpack_archive(expected_archive, expected_data_path)
            # Root directory for expected data of package or dependency
            expected_package_root_dir = os.path.join(
                expected_data_path, os.listdir(expected_data_path)[0]
            )
            assert os.path.isdir(
                expected_package_root_dir
            ), f"Wrong directory path {expected_package_root_dir}."
            # Compare and assert files in directory with expected data
            assert_directories_equal(package_root_dir, expected_package_root_dir)
            # Delete temporary data
            for temp_data in [deps_data_path, expected_data_path, expected_archive]:
                if os.path.exists(deps_data_path):
                    shutil.rmtree(temp_data)


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
    assert (
        content_manifest_response.status == 200
    ), f"#{content_manifest_response.id}: response status {content_manifest_response.status} != 200"

    response_data = content_manifest_response.data
    assert_content_manifest_schema(response_data)
    assert image_contents == content_manifest_response.data["image_contents"], (
        f"#{content_manifest_response.id}: image content in reponse differs from test expactations."
        f"\nResponse image content: "
        f"{json.dumps(content_manifest_response.data['image_contents'], indent=4, sort_keys=True)},"
        f"\nTest expectations: {json.dumps(image_contents, indent=4, sort_keys=True)}"
    )


def assert_properly_completed_response(completed_response):
    """
    Check that the request completed successfully.

    :param Response completed_response: response from the Cachito API
    """
    assert (
        completed_response.status == 200
    ), f"#{completed_response.id}: response status {completed_response.status} != 200"
    assert (
        completed_response.data["state"] == "complete"
    ), f"#{completed_response.id}: response state is {completed_response.data['state']}"
    assert completed_response.data["state_reason"] == "Completed successfully", (
        f"#{completed_response.id}: response state_reason is "
        f"{completed_response.data['state_reason']}"
    )
