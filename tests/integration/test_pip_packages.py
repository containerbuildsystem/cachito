# SPDX-License-Identifier: GPL-3.0-or-later

from os import path, walk
import requests
import tarfile

from utils import Client, assert_content_manifest_schema


def test_pip_package_without_deps(test_env, tmpdir):
    """
    Validate data in the pip package request without dependencies.

    Process:
    Send new request to the Cachito API
    Send request to check status of existing request

    Checks:
    * Check that the request completes successfully
    * Check that a single pip package is identified in response
    * Check response parameters of the package
    * Check that the source tarball includes the application source code
    * Check that the source tarball includes an empty deps/pip directory
    * Check: The content manifest is successfully generated and contains correct content
    """
    client = Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    repo = test_env["pip_packages"]["without_deps"]["repo"]
    ref = test_env["pip_packages"]["without_deps"]["ref"]
    pkg_managers = ["pip"]
    initial_response = client.create_new_request(
        payload={
            "repo": repo,
            "ref": ref,
            "pkg_managers": pkg_managers,
            # TODO: delete pip-dev-preview flag when
            #  the pip package manager will be ready for production usage
            "flags": ["pip-dev-preview"],
        },
    )
    completed_response = client.wait_for_complete_request(initial_response)
    assert completed_response.status == 200

    expected_package_params = [test_env["pip_packages"]["without_deps"]["package"]]
    assert_packages_from_response(completed_response.data, expected_package_params)

    # Download and extract source tarball
    source_name = tmpdir.join(f"download_{str(completed_response.id)}")
    file_name_tar = tmpdir.join(f"download_{str(completed_response.id)}.tar.gz")
    resp = client.download_bundle(completed_response.id, file_name_tar)
    assert resp.status == 200
    assert tarfile.is_tarfile(file_name_tar)
    with tarfile.open(file_name_tar, "r:gz") as tar:
        tar.extractall(source_name)

    expected_file_urls = test_env["pip_packages"]["without_deps"]["expected_files"]

    # Check that the source tarball includes the application source code under the app directory.
    assert_expected_files(path.join(source_name, "app"), expected_file_urls)
    # Check that the source tarball includes an empty deps directory.
    assert_expected_files(path.join(source_name, "deps"))

    purl = test_env["pip_packages"]["without_deps"]["purl"]
    image_contents = [{"dependencies": [], "purl": purl, "sources": []}]
    assert_pip_content_manifest(client, completed_response.id, image_contents)


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


def assert_expected_files(source_path, expected_file_urls=None):
    """
    Check that the source path includes expected files.

    :param str source_path: local path for checking
    :param dict expected_file_urls: {"relative_path/file_name": "URL", ...}
    """
    if expected_file_urls is None:
        expected_file_urls = {}
    assert path.exists(source_path) and path.isdir(source_path)
    files = []
    # Go through all files in source_code_path and it's subdirectories
    for root, _, source_files in walk(source_path):
        for file_name in source_files:
            # Get path to file in the project
            absolute_file_path = path.join(root, file_name)
            relative_file_path = path.relpath(absolute_file_path, start=source_path)
            file_url = expected_file_urls[relative_file_path]
            # Download expected file
            expected_file = requests.get(file_url).content
            # Assert that content of source file is equal to expected
            with open(absolute_file_path, "rb") as f:
                assert f.read() == expected_file
            files.append(relative_file_path)

    # Assert that there are no missing or extra files
    assert set(files) == set(list(expected_file_urls))


def assert_pip_content_manifest(client, request_id, image_contents):
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
