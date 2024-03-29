# SPDX-License-Identifier: GPL-3.0-or-later
import os
import random
import shutil
import string
import subprocess
from contextlib import ExitStack
from pathlib import Path
from textwrap import dedent

import git
import pytest

from . import utils


class TestCachedPackage:
    """Test class for cached package."""

    @pytest.fixture(autouse=True)
    def setup_method_fixture(self, test_env):
        """Create bare git repo and a pool for removing shared directories."""
        self.directories = []
        self.env_data = utils.load_test_data("cached_dependencies.yaml")["cached_package"]
        self.git_user = test_env["git_user"]
        self.git_email = test_env["git_email"]
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
                test_env["api_url"],
                test_env["api_auth_type"],
                test_env.get("timeout"),
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


class TestCachedDependencies:
    """Test class for cached dependencies."""

    @pytest.mark.parametrize(
        "env_name,private",
        [
            ("pip_cached_deps", False),
            ("gomod_cached_deps", False),
            ("npm_cached_deps", False),
            ("yarn_cached_deps", False),
            ("rubygems_cached_deps", False),
            ("private_repo_gomod", True),
        ],
    )
    def test_package_with_cached_deps(self, test_env, tmpdir, env_name: str, private: bool):
        """
        Test a package with cached dependency.

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
        * A single package is identified.
        Dependencies are correctly listed under “.dependencies”
        and under “.packages | select(.type == “<pkg_manager>”) | .dependencies”.
        * The source tarball includes the application source code under the app directory.
        * The source tarball includes the dependencies and dev dependencies source code
        under deps/<pkg_manager> directory.
        * The content manifest is successfully generated and contains correct content.
        * Sbom is successfully generated and contains correct content.
        """
        if private:
            test_data = utils.load_test_data("private_repo_packages.yaml")
            deps_test_envs = test_data["private_repo_test_envs"]
        else:
            test_data = utils.load_test_data("cached_dependencies.yaml")
            deps_test_envs = test_data["cached_deps_test_envs"]
        job_name = str(os.environ.get("JOB_NAME"))
        is_supported_env = any(x in job_name for x in deps_test_envs)
        if not is_supported_env:
            pytest.skip(
                (
                    "This test is only executed in environments that"
                    "have been configured with the credentials needed"
                    "for write access to repositories."
                )
            )

        env_data = test_data[env_name]

        self.git_user = test_env["git_user"]
        self.git_email = test_env["git_email"]

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

        new_dep_commits = create_commits(self.cloned_dep_repo)

        # Push changes
        self.dep_repo_origin = self.cloned_dep_repo.remote(name="origin")
        self.dep_repo_origin.push(self.branch)

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

        replace_rules = update_main_repo(
            env_data, main_repo_dir, tmpdir, new_dep_commits, self.cloned_dep_repo, private
        )

        diff_files = self.cloned_main_repo.git.diff(None, name_only=True)
        for diff_file in diff_files.split("\n"):
            self.cloned_main_repo.git.add(diff_file)
        self.cloned_main_repo.git.commit("-m", "test commit")
        self.main_repo_commit = self.cloned_main_repo.head.object.hexsha
        self.main_repo_origin = self.cloned_main_repo.remote(name="origin")
        self.main_repo_origin.push(self.branch)
        main_version = utils.get_pseudo_version(self.cloned_main_repo, self.main_repo_commit)

        replace_rules.update(
            {"MAIN_REPO_COMMIT": self.main_repo_commit, "MAIN_VERSION": main_version}
        )
        utils.update_expected_data(env_data, replace_rules)

        # Create new Cachito request
        client = utils.Client(
            test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout")
        )
        payload = {
            "repo": env_data["https_main_repo"],
            "ref": self.main_repo_commit,
            "pkg_managers": env_data["pkg_managers"],
        }

        with ExitStack() as defer:
            defer.callback(
                delete_branch_and_check,
                self.branch,
                self.cloned_dep_repo,
                self.dep_repo_origin,
                new_dep_commits,
            )
            defer.callback(
                delete_branch_and_check,
                self.branch,
                self.cloned_main_repo,
                self.main_repo_origin,
                [self.main_repo_commit],
            )
            initial_response = client.create_new_request(payload=payload)
            completed_response = client.wait_for_complete_request(initial_response)

        assert_successful_cached_request(completed_response, env_data, tmpdir, client, private)
        # Create new Cachito request to test cached deps
        initial_response = client.create_new_request(payload=payload)
        completed_response = client.wait_for_complete_request(initial_response)

        assert_successful_cached_request(completed_response, env_data, tmpdir, client, private)


def assert_successful_cached_request(response, env_data, tmpdir, client, private=False):
    """
    Provide all verifications for Cachito request with cached dependencies.

    :param Response response: completed Cachito response
    :param dict env_data: the test data
    :param tmpdir: the path to directory with testing files
    :param Client client: the Cachito client to make requests
    :param bool private: a boolean that denotes if the test is using private repo
    """
    utils.assert_properly_completed_response(response)

    response_data = response.data
    expected_response_data = env_data["response_expectations"]
    utils.assert_elements_from_response(response_data, expected_response_data)

    client.download_and_extract_archive(response.id, tmpdir)
    source_path = tmpdir / f"download_{str(response.id)}"

    if not private:
        expected_files = env_data["expected_files"]
        utils.assert_expected_files(source_path, expected_files, tmpdir)

    image_contents = utils.parse_image_contents(env_data.get("content_manifest"))
    utils.assert_content_manifest(client, response.id, image_contents)

    sbom_components = env_data.get("sbom", [])
    utils.assert_sbom(client, response.id, sbom_components)


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


def update_main_repo(env_data, repo_dir, tmpdir, new_dep_commits, dep_repo, private):
    """
    Update main repo with new dependencies and return replacement rules.

    Changes depend on package manager:
    pip:
        * download dep archive and calculate a hash
        * update requirements.txt with 2 deps (https + VCS)
        * return replacement rules based on commits and hash

    gomod:
        * get a pseudo-version for dependency commit
        * add a new dependency in the go.mod file
        * return replacement rules based on commit and pseudo-version

    npm & yarn:
        * copy original package.json file into two segments
        * insert new dependency in between the two segments
          and paste into package.json file
        * return replacement rules based on commits
    rubygems:
        * insert a new GIT dependency into Gemfile & Gemfile.lock
        * return commit hash of an added dependency

    :param dict env_data: the test data
    :param str repo_dir: path to the main repository
    :param tmpdir: temporary test directory
    :param list new_dep_commits: list of 2 dep commits
    :param dep_repo: Dependency git repository
    :param bool private: a boolean that denotes if the test is using private repo
    :return: replacement rules
    :rtype: dict
    """
    if env_data["pkg_managers"] == ["pip"]:
        # Download the archive with first commit changes
        archive_name = os.path.join(tmpdir, f"{new_dep_commits[0]}.zip")
        utils.download_archive(
            f"{env_data['dep_archive_baseurl']}{new_dep_commits[0]}.zip", archive_name
        )
        # Get the archive hash
        dep_hash = utils.get_sha256_hash_from_file(archive_name)

        # Add new dependencies into the main repo
        with open(os.path.join(repo_dir, "requirements.txt"), "a") as f:
            f.write(
                f"{env_data['dep_archive_baseurl']}{new_dep_commits[0]}"
                f".zip#egg=appr&cachito_hash=sha256:{dep_hash}\n"
            )
            f.write(f"git+{env_data['https_dep_repo']}@{new_dep_commits[1]}#egg=appr\n")

        # return replacement rules
        return {
            "FIRST_DEP_COMMIT": new_dep_commits[0],
            "SECOND_DEP_COMMIT": new_dep_commits[1],
            "FIRST_DEP_HASH": dep_hash,
        }
    elif env_data["pkg_managers"] == ["gomod"]:
        dep_version = utils.get_pseudo_version(dep_repo, new_dep_commits[0])

        with open(os.path.join(repo_dir, "go.mod"), "a") as f:
            go_dep = env_data["https_dep_repo"][len("https://") :]
            if go_dep.endswith(".git"):
                go_dep = go_dep[: -len(".git")]
            f.write(f"require {go_dep} {dep_version} \n")

        result_tidy = subprocess.run(
            ["go", "get", f"{go_dep}@{dep_version}"], cwd=repo_dir, text=True, capture_output=True
        )
        assert result_tidy.returncode == 0, f"The command failed with: {result_tidy.stderr}"

        return {
            "FIRST_DEP_COMMIT": new_dep_commits[0],
            "DEP_VERSION": dep_version,
        }
    elif env_data["pkg_managers"] == ["npm"] or env_data["pkg_managers"] == ["yarn"]:
        new_dep = env_data["test_dependency"]
        if env_data["pkg_managers"] == ["npm"]:
            closing_bracket = "\n  },"
        else:
            closing_bracket = "\n  }"

        with open(os.path.join(repo_dir, "package.json"), "r") as f:
            old_package_json = [line for line in f.read().split(closing_bracket)]
        with open(os.path.join(repo_dir, "package.json"), "w") as f:
            f.write(
                f"{old_package_json[0]},\n"
                f"    {new_dep}{closing_bracket}"
                f"{old_package_json[1]}"
            )

        return {
            "FIRST_DEP_COMMIT": new_dep_commits[0],
            "SECOND_DEP_COMMIT": new_dep_commits[1],
        }
    elif env_data["pkg_managers"] == ["rubygems"]:
        with open(os.path.join(repo_dir, "Gemfile"), "a") as f:
            f.write(f"gem 'my-package', git: '{env_data['https_dep_repo']}'\n")
        with open(os.path.join(repo_dir, "Gemfile.lock"), "r") as f:
            gemlock = f.read()

            gemlock = gemlock.replace(
                "GIT",
                dedent(
                    f"""
                    GIT
                      remote: {env_data['https_dep_repo']}
                      revision: {new_dep_commits[0]}
                      specs:
                        my-package (1.0.0)

                    GIT"""
                ),
            )
            gemlock = gemlock.replace("DEPENDENCIES", "DEPENDENCIES\n  my-package!")

        with open(os.path.join(repo_dir, "Gemfile.lock"), "w") as f:
            f.write(gemlock)

        return {
            "FIRST_DEP_COMMIT": new_dep_commits[0],
        }
    return None


def create_commits(repo):
    """
    Add 2 new commits in repo.

    * 1st for remote source archive dependency
    * 2nd for VCS dependency

    :param repo: repository
    :return: list of 2 commits
    :rtype: list
    """
    new_dep_commits = []
    for _ in range(2):
        repo.git.commit("--allow-empty", m="Commit created in integration test for Cachito")
        new_dep_commits.append(repo.head.object.hexsha)

    return new_dep_commits
