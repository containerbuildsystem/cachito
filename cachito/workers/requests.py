# SPDX-License-Identifier: GPL-3.0-or-later
import logging

import requests
import requests_kerberos
from requests.packages.urllib3.util.retry import Retry

from cachito.workers.config import get_worker_config

log = logging.getLogger(__name__)

# The set is extended version of constant Retry.DEFAULT_ALLOWED_METHODS
# with PATCH and POST methods included.
ALL_REQUEST_METHODS = frozenset(
    {"GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS", "TRACE"}
)
# The set includes only methods which don't modify state of the service.
SAFE_REQUEST_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
DEFAULT_RETRY_OPTIONS = {
    "total": 5,
    "read": 5,
    "connect": 5,
    "backoff_factor": 1.3,
    "status_forcelist": (500, 502, 503, 504),
}


def get_requests_session(auth=False, retry_options={}):
    """
    Create a requests session with authentication (when enabled).

    :param bool auth: configure authentication on the session
    :param dict retry_options: overwrite options for initialization of Retry instance
    :return: the configured requests session
    :rtype: requests.Session
    """
    config = get_worker_config()
    session = requests.Session()
    if auth:
        if config.cachito_auth_type == "kerberos":
            session.auth = requests_kerberos.HTTPKerberosAuth(
                mutual_authentication=requests_kerberos.OPTIONAL
            )
        elif config.cachito_auth_type == "cert":
            session.cert = config.cachito_auth_cert

    retry_options = {**DEFAULT_RETRY_OPTIONS, **retry_options}
    adapter = requests.adapters.HTTPAdapter(max_retries=Retry(**retry_options))
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# These sessions are only for connecting to the internal Cachito API
requests_auth_session = get_requests_session(
    auth=True, retry_options={"allowed_methods": ALL_REQUEST_METHODS}
)
requests_session = get_requests_session(retry_options={"allowed_methods": ALL_REQUEST_METHODS})
