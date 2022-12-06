# SPDX-License-Identifier: GPL-3.0-or-later

import os
from pathlib import Path
from typing import Any

import pytest
from git import Repo

from . import utils


@pytest.mark.parametrize("env_package", ["private_repo_https", "private_repo_ssh"])
def test_private_repos(env_package: str, test_env: dict[str, Any], tmp_path: Path) -> None:
    """
    Validate a cachito request with no package managers to a private repo.

    Process:
    Send new request to the Cachito API
    Send request to check status of existing request

    Checks:
    * Check that the request completes successfully
    * Check that no packages are identified in response
    * Check that no dependencies are identified in response
    * Check that the source tarball includes the application source code. Verify the expected files
      by checking both the ref and diff because we don't have a clone of the private source repo.
    * Check that the source tarball includes empty deps directory
    * Check that the content manifest is successfully generated and empty
    """
    test_data = utils.load_test_data("private_repo_packages.yaml")
    private_repo_test_envs = test_data["private_repo_test_envs"]
    env_data = test_data[env_package]
    is_supported_env = any(x in str(os.environ.get("JOB_NAME")) for x in private_repo_test_envs)
    if not is_supported_env:
        pytest.skip(
            (
                "This test is only executed in environments that "
                "have been configured with the credentials needed "
                "to access private repositories."
            )
        )

    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    payload = {
        "repo": env_data["repo"],
        "ref": env_data["ref"],
        "pkg_managers": [],
        "flags": ["include-git-dir"],
    }

    initial_response = client.create_new_request(payload=payload)
    completed_response = client.wait_for_complete_request(initial_response)

    utils.assert_properly_completed_response(completed_response)
    assert completed_response.data["packages"] == []
    assert completed_response.data["dependencies"] == []

    client.download_and_extract_archive(completed_response.id, tmp_path)
    source_path = tmp_path / f"download_{str(completed_response.id)}"
    repo = Repo(source_path / "app")
    assert repo.head.commit.hexsha == env_data["ref"]
    assert not repo.git.diff()
    assert not os.listdir(source_path / "deps")

    utils.assert_content_manifest(client, completed_response.id, [])
