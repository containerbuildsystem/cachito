# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Any

from cachito.common.utils import get_repo_name

from . import utils


def test_get_latest_request(api_client: utils.Client, test_env: dict[str, Any]) -> None:
    """
    Generates requests and ensures that the requests/latest endpoint returns the most recent one.

    For each package in "various_packages", a request and a duplicate request are generated.
    After all requests have been completed for all packages, the requests/latest endpoint is
    queried for each repo_name/ref combination. The request_id of the final duplicate request
    for each package should match what is returned by the latest endpoint.
    """
    latest_created_request_ids = {}
    repeat_count = 2

    # Generate the requests
    for pkg_manager, package in test_env["various_packages"].items():
        repo_name = get_repo_name(package["repo"])
        for _ in range(repeat_count):
            initial_response = api_client.create_new_request(
                payload={
                    "repo": package["repo"],
                    "ref": package["ref"],
                    "pkg_managers": [pkg_manager],
                },
            )
            completed_response = api_client.wait_for_complete_request(initial_response)
            utils.assert_properly_completed_response(completed_response)
            latest_created_request_ids[(repo_name, package["ref"])] = completed_response.data["id"]

    # Check that the latest is the latest
    for package in test_env["various_packages"].values():
        repo_name = get_repo_name(package["repo"])
        latest_request = api_client.fetch_latest_request(
            repo_name=repo_name, ref=package["ref"]
        ).json()

        assert {
            "id",
            "repo",
            "ref",
            "updated",
        }.issubset(latest_request), "Required fields missing from returned Request"
        assert "configuration_files" not in latest_request, "A verbose Request was returned"
        assert (
            latest_created_request_ids[(repo_name, package["ref"])] == latest_request["id"]
        ), f"id={latest_request['id']} is not the latest request for {(repo_name, package['ref'])}"
