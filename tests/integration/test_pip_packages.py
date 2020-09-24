# SPDX-License-Identifier: GPL-3.0-or-later

from os import path
import tarfile

import utils


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
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
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
    utils.assert_packages_from_response(completed_response.data, expected_package_params)

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
    utils.assert_expected_files(path.join(source_name, "app"), expected_file_urls)
    # Check that the source tarball includes an empty deps directory.
    utils.assert_expected_files(path.join(source_name, "deps"))

    purl = test_env["pip_packages"]["without_deps"]["purl"]
    image_contents = [{"dependencies": [], "purl": purl, "sources": []}]
    utils.assert_content_manifest(client, completed_response.id, image_contents)
