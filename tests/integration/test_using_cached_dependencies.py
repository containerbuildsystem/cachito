# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
import random
import shutil
import string

import git
import pytest

import utils


class TestCachedPackage:
    """Test class for cached package."""

    @pytest.fixture(autouse=True)
    def setup_method_fixture(self, test_env):
        """Create bare git repo and a pool for removing shared directories."""
        self.directories = []
        self.env_data = utils.load_test_data("cached_dependencies.yaml")["cached_package"]
        self.git_user = self.env_data["test_repo"].get("git_user")
        self.git_email = self.env_data["test_repo"].get("git_email")
        if self.env_data["test_repo"].get("use_local"):
            repo_path = create_local_repository(self.env_data["test_repo"]["ssh_url"])
            self.env_data["test_repo"]["ssh_url"] = repo_path
            # Defer cleanups
            self.directories.append(repo_path)

    def teardown_method(self, method):
        """Remove shared directories in the pool."""
        for directory in self.directories:
            shutil.rmtree(directory)

    def test_using_cached_packages(self, tmpdir, test_env):
        """
        Check that the cached packages are used instead of downloading them from repo again.

        Preconditions:
        * On git instance prepare an empty repository

        Process:
        * Clone the package from the upstream repository
        * Create empty commit on new test branch and push it to the prepared repository
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
        repo = git.repo.Repo.clone_from(self.env_data["seed_repo"]["https_url"], tmpdir)
        remote = repo.create_remote("test", url=self.env_data["test_repo"]["ssh_url"])
        assert remote.exists(), f"Remote {remote.name} does not exist"

        # set user configuration, if available
        if self.git_user:
            repo.config_writer().set_value("user", "name", self.git_user).release()
        if self.git_email:
            repo.config_writer().set_value("user", "email", self.git_email).release()

        try:
            repo.create_head(branch_name).checkout()
            repo.git.commit("--allow-empty", m="Commit created in integration test for Cachito")
            repo.git.push("-u", remote.name, branch_name)
            commit = repo.head.commit.hexsha

            client = utils.Client(
                test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"),
            )
            response = client.create_new_request(
                payload={
                    "repo": self.env_data["test_repo"]["https_url"],
                    "ref": commit,
                    "pkg_managers": self.env_data["test_repo"]["pkg_managers"],
                },
            )
            first_response = client.wait_for_complete_request(response)
            utils.assert_properly_completed_response(first_response)
            assert repo.git.branch(
                "-a", "--contains", commit
            ), f"Commit {commit} is not in branches (it should be there)."

        finally:
            delete_branch_and_check(branch_name, repo, remote, [commit])

        response = client.create_new_request(
            payload={
                "repo": self.env_data["test_repo"]["https_url"],
                "ref": commit,
                "pkg_managers": self.env_data["test_repo"]["pkg_managers"],
            },
        )
        second_response = client.wait_for_complete_request(response)
        utils.assert_properly_completed_response(second_response)
        assert first_response.data["ref"] == second_response.data["ref"]
        assert first_response.data["repo"] == second_response.data["repo"]
        assert set(first_response.data["pkg_managers"]) == set(second_response.data["pkg_managers"])
        first_pkgs = utils.make_list_of_packages_hashable(first_response.data["packages"])
        second_pkgs = utils.make_list_of_packages_hashable(second_response.data["packages"])
        assert first_pkgs == second_pkgs
        first_deps = utils.make_list_of_packages_hashable(first_response.data["dependencies"])
        second_deps = utils.make_list_of_packages_hashable(second_response.data["dependencies"])
        assert first_deps == second_deps


class TestPipCachedDependencies:
    """Test class for pip cached dependencies."""

    def teardown_method(self, method):
        """Delete branch with commit in the main repo."""
        if not self.use_local:
            delete_branch_and_check(
                self.branch, self.cloned_main_repo, self.main_repo_origin, [self.main_repo_commit]
            )

    def test_pip_with_cached_deps(self, test_env, tmpdir):
        """
        Test pip package with cached dependency.

        The test verifies that even after deleting dependency
        Cachito will provide cached version.
        The test supports only remote repos. Local version will be skipped.
        Stages:
        1. Make changes in dependency repository:
            * create new branch
            * push 2 new commits
        2. Make changes in requirements.txt in original repository:
            * add VCS and remote source archive dependencies
            based on commits from 1.
            * push changes with new commit
        3. Create Cachito request and verify it [1]
        4. Delete branch in dependency repository
        5. Create Cachito request and verify it [1]
        [1] Verifications:
        * The request completes successfully.
        * A single pip package is identified.
        Dependencies are correctly listed under “.dependencies”
        and under “.packages | select(.type == “pip”) | .dependencies”.
        * The source tarball includes the application source code under the app directory.
        * The source tarball includes the dependencies and dev dependencies source code
        under deps/pip directory.
        * The content manifest is successfully generated and contains correct content.
        """
        env_data = utils.load_test_data("cached_dependencies.yaml")["cached_deps"]
        self.use_local = env_data["use_local"]
        if self.use_local:
            pytest.skip("The local repos are not supported for the test")

        self.git_user = env_data.get("git_user")
        self.git_email = env_data.get("git_email")

        # Download dependency repo into a new directory
        dep_repo_dir = os.path.join(tmpdir, "dep")
        generated_suffix = "".join(
            random.choice(string.ascii_letters + string.digits) for _ in range(10)
        )
        self.branch = f"test-{generated_suffix}"
        self.cloned_dep_repo = clone_repo_in_new_dir(
            env_data["ssh_dep_repo"], self.branch, dep_repo_dir
        )
        # set user configuration, if available
        if self.git_user:
            self.cloned_dep_repo.config_writer().set_value("user", "name", self.git_user).release()
        if self.git_email:
            self.cloned_dep_repo.config_writer().set_value(
                "user", "email", self.git_email
            ).release()

        # Make changes in dependency repo
        # We need 2 commits:
        # 1st for remote source archive dependency
        # 2nd for VCS dependency
        new_dep_commits = []
        for _ in range(2):
            self.cloned_dep_repo.git.commit(
                "--allow-empty", m="Commit created in integration test for Cachito"
            )
            new_dep_commits.append(self.cloned_dep_repo.head.object.hexsha)

        # Push changes
        self.dep_repo_origin = self.cloned_dep_repo.remote(name="origin")
        self.dep_repo_origin.push(self.branch)

        # Download the archive with first commit changes
        archive_name = os.path.join(tmpdir, f"{new_dep_commits[0]}.zip")
        utils.download_archive(
            f"{env_data['dep_archive_baseurl']}{new_dep_commits[0]}.zip", archive_name
        )
        # Get the archive hash
        dep_hash = utils.get_sha256_hash_from_file(archive_name)

        # Download the main repo into a new dir
        main_repo_dir = os.path.join(tmpdir, "main")
        self.cloned_main_repo = clone_repo_in_new_dir(
            env_data["ssh_main_repo"], self.branch, main_repo_dir
        )
        if self.git_user:
            self.cloned_main_repo.config_writer().set_value("user", "name", self.git_user).release()
        if self.git_email:
            self.cloned_main_repo.config_writer().set_value(
                "user", "email", self.git_email
            ).release()

        # Add new dependencies into the main repo
        with open(os.path.join(main_repo_dir, "requirements.txt"), "a") as f:
            f.write(
                f"{env_data['dep_archive_baseurl']}{new_dep_commits[0]}"
                f".zip#egg=appr&cachito_hash=sha256:{dep_hash}\n"
            )
            f.write(f"git+{env_data['https_dep_repo']}@{new_dep_commits[1]}#egg=appr\n")
        diff_files = self.cloned_main_repo.git.diff(None, name_only=True)
        self.cloned_main_repo.git.add(diff_files)
        self.cloned_main_repo.git.commit("-m", "test commit")
        self.main_repo_commit = self.cloned_main_repo.head.object.hexsha
        self.main_repo_origin = self.cloned_main_repo.remote(name="origin")
        self.main_repo_origin.push(self.branch)

        # Create new Cachito request
        client = utils.Client(
            test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout")
        )
        payload = {
            "repo": env_data["https_main_repo"],
            "ref": self.main_repo_commit,
            "pkg_managers": env_data["pkg_managers"],
        }
        try:
            initial_response = client.create_new_request(payload=payload)
            completed_response = client.wait_for_complete_request(initial_response)
        finally:
            # Delete the dependency branch
            delete_branch_and_check(
                self.branch, self.cloned_dep_repo, self.dep_repo_origin, new_dep_commits
            )

        replace_rules = {
            "FIRST_DEP_COMMIT": new_dep_commits[0],
            "SECOND_DEP_COMMIT": new_dep_commits[1],
            "FIRST_DEP_HASH": dep_hash,
            "MAIN_REPO_COMMIT": self.main_repo_commit,
        }
        update_expected_data(env_data, replace_rules)
        assert_successful_cached_request(completed_response, env_data, tmpdir, client)
        # Create new Cachito request to test cached deps
        initial_response = client.create_new_request(payload=payload)
        completed_response = client.wait_for_complete_request(initial_response)

        assert_successful_cached_request(completed_response, env_data, tmpdir, client)


def assert_successful_cached_request(response, env_data, tmpdir, client):
    """
    Provide all verifications for Cachito request with cached dependencies.

    :param Response response: completed Cachito response
    :param dict env_data: the test data
    :param tmpdir: the path to directory with testing files
    :param Client client: the Cachito client to make requests
    """
    utils.assert_properly_completed_response(response)

    response_data = response.data
    expected_response_data = env_data["response_expectations"]
    utils.assert_elements_from_response(response_data, expected_response_data)

    client.download_and_extract_archive(response.id, tmpdir)
    source_path = tmpdir.join(f"download_{str(response.id)}")
    expected_files = env_data["expected_files"]
    utils.assert_expected_files(source_path, expected_files, tmpdir)

    purl = env_data["purl"]
    deps_purls = []
    source_purls = []
    if "dep_purls" in env_data:
        deps_purls = [{"purl": x} for x in env_data["dep_purls"]]
    if "source_purls" in env_data:
        source_purls = [{"purl": x} for x in env_data["source_purls"]]

    image_contents = [{"dependencies": deps_purls, "purl": purl, "sources": source_purls}]
    utils.assert_content_manifest(client, response.id, image_contents)


def clone_repo_in_new_dir(ssh_repo, branch, repo_dir):
    """
    Clone repo in new directory and open special branch.

    :param str ssh_repo: SSH repository for cloning
    :param str branch: The name of new branch to open
    :param str repo_dir: Name of new directory to create
    :return cloned_dep_repo: git.Repo what was cloned
    """
    os.mkdir(repo_dir)
    cloned_dep_repo = git.Repo.clone_from(ssh_repo, repo_dir)
    # Open a new branch in repo
    cloned_dep_repo.git.checkout("-b", branch)
    assert cloned_dep_repo.active_branch.name == branch
    return cloned_dep_repo


def create_local_repository(repo_path):
    """
    Create a local git repoitory.

    :param str repo_path: path to new bare git repository
    :return: normalized bare git repository path
    :rtype: str
    """
    bare_repo_dir = Path(repo_path)
    bare_repo = git.Repo.init(str(bare_repo_dir), bare=True)
    assert bare_repo.bare, f"{bare_repo} is not bare repository"
    # We need to expand this for later usage from the original repo directory
    return str(bare_repo_dir.resolve())


def delete_branch_and_check(branch, repo, remote, commits):
    """
    Delete remote branch and check that commits were deleted.

    :param str branch: Remote branch to delete
    :param git.Repo repo: Git repository
    :param remote: Git remote with branch to delete
    :param list commits: List of commits to check were deleted
    """
    repo.git.push("--delete", remote.name, branch)
    repo.heads.master.checkout()
    repo.git.branch("-D", branch)
    for commit in commits:
        assert not repo.git.branch(
            "-a", "--contains", commit
        ), f"Commit {commit} is still in a branch (it shouldn't be there at this point)."


def replace_by_rules(orig_str, replace_rules):
    """
    Replace elements in string according to replace rules.

    :param str orig_str: original string
    :param dict replace_rules: replace rules as a dictionary:
        {<ORIG_PART>: <NEW_PART>}
    :return: string with replaced values
    :rtype: str
    """
    res_string = orig_str
    for s, r in replace_rules.items():
        if s in res_string:
            res_string = res_string.replace(s, r)
    return res_string


def update_expected_data(env_data, replace_rules):
    """
    Update expected data for the test in place.

    Change commits and hashes in:
    * expected_files
    * response_expectations
    * all purls in env_data
    :param dict env_data: the test data
    :param dict replace_rules: replace rules as a dictionary:
        {<ORIG_PART>: <NEW_PART>}
    """
    new_expected_files = {}
    for file, url in env_data["expected_files"].items():
        new_expected_files[replace_by_rules(file, replace_rules)] = replace_by_rules(
            url, replace_rules
        )
    env_data["expected_files"] = new_expected_files

    for i, dep in enumerate(env_data["response_expectations"]["dependencies"]):
        env_data["response_expectations"]["dependencies"][i]["version"] = replace_by_rules(
            dep["version"], replace_rules
        )
    for i, dep in enumerate(env_data["response_expectations"]["packages"][0]["dependencies"]):
        env_data["response_expectations"]["packages"][0]["dependencies"][i][
            "version"
        ] = replace_by_rules(dep["version"], replace_rules)

    env_data["purl"] = replace_by_rules(env_data["purl"], replace_rules)
    for i, p in enumerate(env_data["dep_purls"]):
        env_data["dep_purls"][i] = replace_by_rules(p, replace_rules)
    for i, p in enumerate(env_data["source_purls"]):
        env_data["source_purls"][i] = replace_by_rules(p, replace_rules)
