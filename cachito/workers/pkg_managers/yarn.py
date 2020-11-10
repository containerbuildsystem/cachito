import logging

from cachito.workers.config import get_worker_config

__all__ = ["get_yarn_proxy_repo_name", "get_yarn_proxy_repo_url", "get_yarn_proxy_repo_username"]

log = logging.getLogger(__name__)


def get_yarn_proxy_repo_name(request_id):
    """
    Get the name of yarn proxy repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the cachito-yarn-<REQUEST_ID> string, representing the temporary repository name
    :rtype: str
    """
    config = get_worker_config()
    return f"{config.cachito_nexus_request_repo_prefix}yarn-{request_id}"


def get_yarn_proxy_repo_url(request_id):
    """
    Get the URL for the Nexus yarn proxy repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the URL for the Nexus cachito-yarn-<REQUEST_ID> repository
    :rtype: str
    """
    config = get_worker_config()
    repo_name = get_yarn_proxy_repo_name(request_id)
    return f"{config.cachito_nexus_url.rstrip('/')}/repository/{repo_name}/"


def get_yarn_proxy_repo_username(request_id):
    """
    Get the username that has read access on the yarn proxy repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the cachito-yarn-<REQUEST_ID> string, representing the user
        who will access the temporary Nexus repository
    :rtype: str
    """
    return f"cachito-yarn-{request_id}"
