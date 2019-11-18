# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
from abc import ABC, abstractmethod
import urllib.parse
import tarfile
import tempfile

import git

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config


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
        self._archives_dir = None
        self._archive_path = None
        self._repo_name = None

    @property
    def archive_name(self):
        """
        Get what the archive name should be for a particular SCM reference.

        :return: the archive name
        :rtype: str
        """
        return f'{self.ref}.tar.gz'

    @property
    def archive_path(self):
        """
        Get the path to where the archive for a particular SCM reference should be.

        :return: the path to the archive
        :rtype: str
        """
        if not self._archive_path:
            directory = os.path.join(self.archives_dir, *self.repo_name.split('/'))
            # Create the directories if they don't exist
            os.makedirs(directory, exist_ok=True)
            self._archive_path = os.path.join(directory, self.archive_name)

        return self._archive_path

    @property
    def archives_dir(self):
        """
        Get the absolute path of the archives directory from the Celery configuration.

        :returns: the absolute path of the archives directory
        :rtype: str
        """
        if not self._archives_dir:
            self._archives_dir = os.path.abspath(
                get_worker_config().cachito_sources_dir
            )
            log.debug('Using "%s" as the archives directory', self._archives_dir)

        return self._archives_dir

    @abstractmethod
    def fetch_source(self):
        """
        Fetch the repo, create a compressed tar file, and put it in long-term storage.
        """

    @property
    @abstractmethod
    def repo_name(self):
        """
        Determine the repo name based on the URL
        """


class Git(SCM):
    """The git implementation of interacting with source control."""

    def clone_and_archive(self):
        """
        Clone the git repository and create the compressed source archive.

        :raises CachitoError: if cloning the repository fails or if the archive can't be created
        """
        with tempfile.TemporaryDirectory(prefix='cachito-') as temp_dir:
            clone_path = os.path.join(temp_dir, 'repo')
            log.debug('Cloning the Git repository from %s', self.url)
            # Don't allow git to prompt for a username if we don't have access
            os.environ['GIT_TERMINAL_PROMPT'] = '0'
            try:
                repo = git.repo.Repo.clone_from(self.url, clone_path, no_checkout=True)
            except:  # noqa E722
                log.exception('Cloning the Git repository from %s failed', self.url)
                raise CachitoError('Cloning the Git repository failed')

            try:
                repo.head.reference = repo.commit(self.ref)
                repo.head.reset(index=True, working_tree=True)
            except:  # noqa E722
                log.exception('Checking out the Git ref "%s" failed', self.ref)
                raise CachitoError(
                    'Checking out the Git repository failed. Please verify the supplied reference '
                    f'of "{self.ref}" is valid.'
                )

            log.debug('Creating the archive at %s', self.archive_path)
            with tarfile.open(self.archive_path, mode='w:gz') as bundle_archive:
                bundle_archive.add(clone_path, 'app')

    def fetch_source(self):
        """
        Fetch the repo, create a compressed tar file, and put it in long-term storage.
        """
        # If it already exists and isn't corrupt, don't download it again
        if os.path.exists(self.archive_path) and tarfile.is_tarfile(self.archive_path):
            log.debug('The archive already exists at "%s"', self.archive_path)
            return

        self.clone_and_archive()

    @property
    def repo_name(self):
        """
        Determine the repo name based on the URL
        """
        if not self._repo_name:
            parsed_url = urllib.parse.urlparse(self.url)
            repo = parsed_url.path.strip('/')
            if repo.endswith('.git'):
                repo = repo[: -len('.git')]
            self._repo_name = repo
            log.debug('Parsed the repository name "%s" from %s', self._repo_name, self.url)

        return self._repo_name
