# SPDX-License-Identifier: GPL-3.0-or-later
import logging

import git

from cachito.workers.pkg_managers.general import update_request_with_package
from cachito.workers.paths import RequestBundleDir
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.utils import runs_if_request_in_progress

__all__ = ["add_git_submodules_as_package"]
log = logging.getLogger(__name__)


@app.task
@runs_if_request_in_progress
def add_git_submodules_as_package(request_id):
    """
    Add git submodules as package to the Cachtio request.

    :param int request_id: the Cachito request ID this is for
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
        update_request_with_package(request_id, package, package_subpath=sm.path)
