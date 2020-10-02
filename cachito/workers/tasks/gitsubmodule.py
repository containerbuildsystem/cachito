# SPDX-License-Identifier: GPL-3.0-or-later
import logging

import git

from cachito.workers.pkg_managers.general import update_request_with_package
from cachito.workers.paths import RequestBundleDir
from cachito.workers.tasks.celery import app

__all__ = ["add_git_submodules_as_package"]
log = logging.getLogger(__name__)


@app.task
def add_git_submodules_as_package(request_id, url, ref):
    """
    Add git submodules as package to the Cachtio request.

    :param int request_id: the Cachito request ID this is for
    :param str url: the source control URL to pull the source from
    :param str ref: the source control reference
    :raises CachitoError: if adding submodules as a package fail.
    """
    bundle_dir = RequestBundleDir(request_id)
    repo = git.Repo(str(bundle_dir.source_root_dir))
    for sm in repo.submodules:
        # Save package to db
        package = {
            "type": "git-submodule",
            "name": sm.name,
            "version": f"{sm.url}#{sm.hexsha}",
        }
        log.debug("Adding submodule '%s' as a package for Cachito request", sm.name)
        update_request_with_package(request_id, package)
