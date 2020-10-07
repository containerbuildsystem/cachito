# SPDX-License-Identifier: GPL-3.0-or-later
from abc import ABC, abstractmethod
import logging
import os
import subprocess
import tarfile
import tempfile
import urllib.parse

import git

from cachito.errors import CachitoError
from cachito.workers.paths import SourcesDir


log = logging.getLogger(__name__)


class SCM(ABC):
    """The base class for interacting with source control."""

    def __init__(self, url, ref):
        """
        Initialize the SCM class.

        :param str url: the source control URL to the repository to fetch
        :param str ref: the source control reference
        """
        super().__init__()
        self.url = url
        self.ref = ref
        self._repo_name = None

        self.sources_dir = SourcesDir(self.repo_name, ref)

    @abstractmethod
    def fetch_source(self):
        """Fetch the repo, create a compressed tar file, and put it in long-term storage."""

    @property
    @abstractmethod
    def repo_name(self):
        """Determine the repo name based on the URL."""


class Git(SCM):
    """The git implementation of interacting with source control."""

    def _reset_git_head(self, repo):
        """
        Reset HEAD to a specific Git reference.

        :param git.Repo repo: the repository object.
        :raises CachitoError: if changing the HEAD of the repository fails.
        """
        try:
            repo.head.reference = repo.commit(self.ref)
            repo.head.reset(index=True, working_tree=True)
        except:  # noqa E722
            log.exception('Checking out the Git ref "%s" failed', self.ref)
            raise CachitoError(
                "Checking out the Git repository failed. Please verify the supplied reference "
                f'of "{self.ref}" is valid.'
            )

    def _verify_archive(self):
        """
        Verify the archive containing the git repository.

        :raises CachitoError: if 'git fsck' fails for the extracted sources
        """
        log.debug("Verifying the archive at %s", self.sources_dir.archive_path)
        if not os.path.exists(self.sources_dir.archive_path) or not tarfile.is_tarfile(
            self.sources_dir.archive_path
        ):
            err_msg = f"No valid archive found at {self.sources_dir.archive_path}"
            log.exception(err_msg)
            raise CachitoError(err_msg)

        with tempfile.TemporaryDirectory(prefix="cachito-") as temp_dir:
            with tarfile.open(self.sources_dir.archive_path, mode="r:gz") as tar:
                tar.extractall(temp_dir)

            cmd = ["git", "fsck"]
            repo_path = os.path.join(temp_dir, "app")
            try:
                subprocess.run(cmd, cwd=repo_path, check=True, capture_output=True)
            except subprocess.CalledProcessError as exc:
                log.error(
                    "Cachito found an error when verifying the generated archive at '%s': %s - %s",
                    self.sources_dir.archive_path,
                    exc,
                    exc.stderr,
                )
                raise CachitoError(f"Invalid archive at {self.sources_dir.archive_path!s}")

    def _create_archive(self, from_dir):
        """
        Create a verified archive from a specified directory.

        :param str from_dir: path to a directory from where to create the archive.
        :raises CachitoError: if the archive verification fails
        """
        log.debug("Creating the archive at %s", self.sources_dir.archive_path)
        with tarfile.open(self.sources_dir.archive_path, mode="w:gz") as bundle_archive:
            bundle_archive.add(from_dir, "app")
        try:
            self._verify_archive()
        except CachitoError:
            log.debug("Removing invalid archive at %s", self.sources_dir.archive_path)
            os.unlink(self.sources_dir.archive_path)
            raise

    def clone_and_archive(self, gitsubmodule=False):
        """
        Clone the git repository and create the compressed source archive.

        :param bool gitsubmodule: a bool to determine whether git submodules need to be processed.
        :raises CachitoError: if cloning the repository fails or if the archive can't be created
        """
        with tempfile.TemporaryDirectory(prefix="cachito-") as temp_dir:
            log.debug("Cloning the Git repository from %s", self.url)
            clone_path = os.path.join(temp_dir, "repo")
            try:
                repo = git.repo.Repo.clone_from(
                    self.url,
                    clone_path,
                    no_checkout=True,
                    # Don't allow git to prompt for a username if we don't have access
                    env={"GIT_TERMINAL_PROMPT": "0"},
                )
            except:  # noqa E722
                log.exception("Cloning the Git repository from %s failed", self.url)
                raise CachitoError("Cloning the Git repository failed")

            self._reset_git_head(repo)

            if gitsubmodule:
                self.update_git_submodules(repo)

            self._create_archive(repo.working_dir)

    def update_and_archive(self, previous_archive, gitsubmodule=False):
        """
        Update the existing Git history and create a source archive.

        :param str previous_archive: path to an archive file created before.
        :param bool gitsubmodule: a bool to determine whether git submodules need to be processed.
        :raises CachitoError: if pulling the Git history from the remote repo or
            the checkout of the target Git ref fails.
        """
        with tempfile.TemporaryDirectory(prefix="cachito-") as temp_dir:
            with tarfile.open(previous_archive, mode="r:gz") as tar:
                tar.extractall(temp_dir)

            repo = git.Repo(os.path.join(temp_dir, "app"))
            try:
                # The reference must be specified to handle commits which are not part
                # of a branch.
                repo.remote().fetch(refspec=self.ref)
            except:  # noqa E722
                log.exception("Failed to fetch from remote %s", self.url)
                raise CachitoError("Failed to fetch from the remote Git repository")

            self._reset_git_head(repo)
            if gitsubmodule:
                self.update_git_submodules(repo)

            self._create_archive(repo.working_dir)

    def fetch_source(self, gitsubmodule=False):
        """Fetch the repo, create a compressed tar file, and put it in long-term storage.

        :param bool gitsubmodule: a bool to determine whether git submodules need to be processed.
        """
        if gitsubmodule:
            self.sources_dir = SourcesDir(self.repo_name, f"{self.ref}-with-submodules")

        # If it already exists and isn't corrupt, don't download it again
        archive_path = self.sources_dir.archive_path
        if archive_path.exists():
            log.debug('The archive already exists at "%s"', archive_path)
            try:
                self._verify_archive()
                return
            except CachitoError:
                log.warning('The archive at "%s" is invalid and will be re-created', archive_path)

        # Find a previous archive created by a previous request
        #
        # The previous archive does not mean the one just before the request that
        # schedules current task. The only reason for finding out such a file is
        # to access the git history. So, anyone is ok.
        previous_archives = sorted(
            self.sources_dir.package_dir.glob("*.tar.gz"), key=os.path.getctime, reverse=True
        )
        for previous_archive in previous_archives:
            # Excluding previous archives with submodules since there is
            # no straight-forward way to get rid of the previously included
            # submodules
            if "-with-submodules" in str(previous_archive):
                continue
            try:
                self.update_and_archive(previous_archive, gitsubmodule=gitsubmodule)
                return
            except (
                git.exc.InvalidGitRepositoryError,
                tarfile.ExtractError,
                OSError,  # raised by tarfile when an FS operation fails
            ) as exc:
                log.warning(
                    "Error handling archived artifact '%s': %s - %s",
                    previous_archive,
                    repr(exc),
                    exc,
                )
                log.info(
                    "Existing archive at '%s' may be corrupted and will not be used. Recovering...",
                    previous_archive,
                )

        self.clone_and_archive(gitsubmodule=gitsubmodule)

    def update_git_submodules(self, repo):
        """Update git submodules.

        For the given repo, update submodules. For Cachito request for `retrodep` repo and
        `go-github` submodule, the dir structure would look like,
        retrodep/go-github/<content_of_go-github_repo>

        :param git.Repo repo: the repository object.
        :raises CachitoError: if updating the git submodules fail.
        """
        try:
            log.debug(f"Git submodules for the requested repo are: {repo.submodules}")
            repo.submodule_update(recursive=False)
        except Exception as e:
            log.exception("Updating the Git submodule(s) from %s failed %s", self.url, e)
            raise CachitoError("Updating the Git submodule(s) failed")

    @property
    def repo_name(self):
        """Determine the repo name based on the URL."""
        if not self._repo_name:
            parsed_url = urllib.parse.urlparse(self.url)
            repo = parsed_url.path.strip("/")
            if repo.endswith(".git"):
                repo = repo[: -len(".git")]
            self._repo_name = repo
            log.debug('Parsed the repository name "%s" from %s', self._repo_name, self.url)

        return self._repo_name
