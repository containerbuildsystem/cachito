# SPDX-License-Identifier: GPL-3.0-or-later

import random
import string

import git

import utils


def test_using_cached_dependencies(test_env, tmpdir):
    """
    Check that the cached dependencies are used instead of downloading them from repo again.

    Preconditions:
    * On git instance prepare an empty repository

    Process:
    * Clone the package from the upstream repository
    * Create empty commit on new test branch and push it to prepared repository
    * Send new request to Cachito API which would fetch data from the prepared repository
    * Delete branch with the corresponding commit
    * Send the same request to Cachito API

    Checks:
    * Check that the state of the first request is complete
    * Check that the commit is not available in the repository after the branch is deleted
    * Check that the state of the second request is complete
    """
    generated_suffix = "".join(
        random.choice(string.ascii_letters + string.digits) for x in range(10)
    )
    branch_name = f"test-{generated_suffix}"
    repo = git.repo.Repo.clone_from(
        test_env["cached_dependencies"]["seed_repo"]["https_url"], tmpdir
    )
    remote = repo.create_remote("test", url=test_env["cached_dependencies"]["test_repo"]["ssh_url"])
    assert remote.exists()

    try:
        repo.create_head(branch_name).checkout()
        repo.git.commit("--allow-empty", m="Commit created in integration test for Cachito")
        repo.git.push("-u", remote.name, branch_name)
        commit = repo.head.commit.hexsha

        client = utils.Client(test_env["api_url"], test_env["api_auth_type"])
        response = client.create_new_request(
            payload={
                "repo": test_env["cached_dependencies"]["test_repo"]["https_url"],
                "ref": commit,
                "pkg_managers": test_env["cached_dependencies"]["test_repo"]["pkg_managers"],
            },
        )
        first_response = client.wait_for_complete_request(response)
        assert first_response.data["state"] == "complete"

        assert repo.git.branch("-a", "--contains", commit)

    finally:
        repo.git.push("--delete", remote.name, branch_name)

    repo.heads.master.checkout()
    repo.git.branch("-D", branch_name)
    assert not repo.git.branch("-a", "--contains", commit)

    response = client.create_new_request(
        payload={
            "repo": test_env["cached_dependencies"]["test_repo"]["https_url"],
            "ref": commit,
            "pkg_managers": test_env["cached_dependencies"]["test_repo"]["pkg_managers"],
        },
    )
    second_response = client.wait_for_complete_request(response)
    assert second_response.data["state"] == "complete"

    assert first_response.data["ref"] == second_response.data["ref"]
    assert first_response.data["repo"] == second_response.data["repo"]
    assert first_response.data["pkg_managers"] == second_response.data["pkg_managers"]
    assert first_response.data["packages"] == second_response.data["packages"]
    assert first_response.data["dependencies"] == second_response.data["dependencies"]
