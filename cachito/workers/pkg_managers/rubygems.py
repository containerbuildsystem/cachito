import logging

from cachito.errors import CachitoError
from cachito.workers import nexus
from cachito.workers.errors import NexusScriptError

log = logging.getLogger(__name__)


def prepare_nexus_for_rubygems_request(rubygems_repo_name, raw_repo_name):
    """
    Prepare Nexus so that Cachito can stage Rubygems content.

    :param str rubygems_repo_name: the name of the Rubygems repository for the request
    :param str raw_repo_name: the name of the raw repository for the request
    :raise CachitoError: if the script execution fails
    """
    payload = {
        "rubygems_repository_name": rubygems_repo_name,
        "raw_repository_name": raw_repo_name,
    }
    script_name = "rubygems_before_content_staged"
    try:
        nexus.execute_script(script_name, payload)
    except NexusScriptError:
        log.exception("Failed to execute the script %s", script_name)
        raise CachitoError("Failed to prepare Nexus for Cachito to stage Rubygems content")
