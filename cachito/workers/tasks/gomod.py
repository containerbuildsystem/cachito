# SPDX-License-Identifier: GPL-3.0-or-later
import logging

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config
from cachito.workers.pkg_managers.general import (
    update_request_with_deps,
    update_request_with_package,
)
from cachito.workers.pkg_managers.gomod import resolve_gomod
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.general import set_request_state
from cachito.workers.paths import RequestBundleDir

__all__ = ["fetch_gomod_source"]
log = logging.getLogger(__name__)


@app.task
def fetch_gomod_source(request_id, dep_replacements=None):
    """
    Resolve and fetch gomod dependencies for a given request.

    :param int request_id: the Cachito request ID this is for
    :param list dep_replacements: dependency replacements with the keys "name" and "version"
    :raises CachitoError: if the dependencies could not be retrieved
    """
    config = get_worker_config()
    bundle_dir = RequestBundleDir(request_id)

    if not bundle_dir.go_mod_file.exists():
        if config.cachito_gomod_ignore_missing_gomod_file:
            log.warning("The go.mod file is missing for request %d", request_id)
            return

        raise CachitoError("The go.mod file is missing")

    log.info("Fetching gomod dependencies for request %d", request_id)
    request = set_request_state(request_id, "in_progress", "Fetching the gomod dependencies")
    try:
        gomod = resolve_gomod(str(bundle_dir.source_dir), request, dep_replacements)
    except CachitoError:
        log.exception("Failed to fetch gomod dependencies for request %d", request_id)
        raise

    env_vars = {
        "GOCACHE": {"value": "deps/gomod", "kind": "path"},
        "GOPATH": {"value": "deps/gomod", "kind": "path"},
    }
    env_vars.update(config.cachito_default_environment_variables.get("gomod", {}))
    update_request_with_package(request_id, gomod["module"], env_vars)
    update_request_with_deps(request_id, gomod["module"], gomod["module_deps"])

    # add package deps
    update_request_with_deps(request_id, gomod["pkg"], gomod["pkg_deps"])
