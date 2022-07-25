# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from . import utils


@pytest.mark.parametrize(
    "env_package,env_name",
    [
        ("pip_packages", "without_deps"),
        ("pip_packages", "with_deps"),
        ("pip_packages", "multiple"),
        ("pip_packages", "git_submodule"),
        ("go_generate_packages", "go_generate"),
        ("go_generate_packages", "go_generate_generated"),
        ("go_generate_packages", "go_generate_imported"),
        ("go_generate_packages", "go_generate_imported_generated"),
        ("gomod_packages", "without_deps"),
        pytest.param(
            "gomod_packages",
            "without_deps_vendor_check",
            marks=pytest.mark.xfail(
                reason="Cachito currently cannot handle vendoring when there is nothing to vendor"
            ),
        ),
        ("gomod_packages", "with_deps"),
        ("gomod_packages", "vendored_with_flag"),
        ("gomod_packages", "implicit_gomod"),
        ("gomod_packages", "missing_gomod"),
        ("gomod_packages", "force_tidy_without_deps"),
        ("gomod_packages", "force_tidy_vendored"),
        ("gomod_packages", "symlink_loop"),
        ("gomod_packages", "without_pkg_manager"),
        ("gomod_vendor_check", "correct_vendor"),
        ("gomod_vendor_check", "no_vendor"),
        ("npm_packages", "without_deps"),
        ("npm_packages", "with_deps"),
        ("npm_packages", "git_submodule"),
        ("npm_packages", "workspaces"),
        ("yarn_packages", "without_deps"),
        ("yarn_packages", "with_deps"),
        ("yarn_packages", "git_submodule"),
    ],
)
def test_packages(env_package, env_name, test_env, tmpdir):
    """
    Validate data in the package request according to pytest env_name and env_package parameter.

    Process:
    Send new request to the Cachito API
    Send request to check status of existing request

    Checks:
    * Check that the request completes successfully
    * Check that expected packages are identified in response
    * Check that expected dependencies are identified in response
    * Check response parameters of the package
    * Check that the source tarball includes the application source code
    * Check that the source tarball includes expected deps directory
    * Check: The content manifest is successfully generated and contains correct content
    """
    env_data = utils.load_test_data(f"{env_package}.yaml")[env_name]
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))

    payload = {
        "repo": env_data["repo"],
        "ref": env_data["ref"],
        "pkg_managers": env_data.get("pkg_managers", []),
    }
    if "flags" in env_data:
        payload["flags"] = env_data["flags"]

    # Add packages to Cachito request if possible
    if "packages" in env_data:
        payload["packages"] = env_data["packages"]

    if env_name == "implicit_gomod":
        payload.pop("pkg_managers")

    initial_response = client.create_new_request(payload=payload)
    completed_response = client.wait_for_complete_request(initial_response)
    response_data = completed_response.data
    expected_response_data = env_data["response_expectations"]
    utils.assert_elements_from_response(response_data, expected_response_data)

    client.download_and_extract_archive(completed_response.id, tmpdir)
    source_path = tmpdir.join(f"download_{str(completed_response.id)}")
    expected_files = env_data["expected_files"]
    utils.assert_expected_files(source_path, expected_files, tmpdir)

    image_contents = []
    for pkg in env_data.get("content_manifest"):
        purl = pkg.get("purl", "")
        dep_purls = []
        source_purls = []
        if "dep_purls" in pkg:
            dep_purls = [{"purl": x} for x in pkg["dep_purls"]]
        if "source_purls" in pkg:
            source_purls = [{"purl": x} for x in pkg["source_purls"]]
        if purl:
            image_contents.append(
                {"dependencies": dep_purls, "purl": purl, "sources": source_purls}
            )
    utils.assert_content_manifest(client, completed_response.id, image_contents)
