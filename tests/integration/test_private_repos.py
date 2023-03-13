# SPDX-License-Identifier: GPL-3.0-or-later

import os
import random
import string
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
    Create new commit at "cachito-no-package-manager-private" repo
        (To prevent cachito from caching private source code and serving
        it without trying to access the repository, more info: STONEBLD-661)
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
    job_name = str(os.environ.get("JOB_NAME"))
    is_supported_env = any(x in job_name for x in private_repo_test_envs)
    if not is_supported_env:
        pytest.skip(
            (
                "This test is only executed in environments that "
                "have been configured with the credentials needed "
                "to access private repositories."
            )
        )

    repo = Repo.clone_from(test_data["private_repo_ssh"]["repo"], tmp_path)

    repo.config_writer().set_value("user", "name", test_env["git_user"]).release()
    repo.config_writer().set_value("user", "email", test_env["git_email"]).release()

    generated_suffix = "".join(
        random.choice(string.ascii_letters + string.digits) for x in range(10)
    )
    branch_name = f"tmp-branch-{generated_suffix}"

    try:
        repo.create_head(branch_name).checkout()

        message = "Committed by Cachito integration test (test_private_repos)"
        repo.git.commit("--allow-empty", m=message)
        repo.git.push("-u", "origin", branch_name)

        ref = repo.head.commit.hexsha

        client = utils.Client(
            test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout")
        )
        payload = {
            "repo": env_data["repo"],
            "ref": ref,
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
        downloaded_repo = Repo(source_path / "app")
        assert downloaded_repo.head.commit.hexsha == ref
        assert not downloaded_repo.git.diff()
        assert not os.listdir(source_path / "deps")

        utils.assert_content_manifest(client, completed_response.id, [])

    finally:
        repo.git.push("--delete", "origin", branch_name)
