# SPDX-License-Identifier: GPL-3.0-or-later
from cachito.workers import nexus
from cachito.workers.pkg_managers.yarn import (
    get_yarn_proxy_repo_name,
    get_yarn_proxy_repo_username,
)
from cachito.workers.tasks.celery import app

__all__ = ["cleanup_yarn_request"]


@app.task
def cleanup_yarn_request(request_id):
    """Clean up the Nexus yarn content for the Cachito request."""
    payload = {
        "repository_name": get_yarn_proxy_repo_name(request_id),
        "username": get_yarn_proxy_repo_username(request_id),
    }
    nexus.execute_script("js_cleanup", payload)
