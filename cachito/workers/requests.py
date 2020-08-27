# SPDX-License-Identifier: GPL-3.0-or-later
import logging

import requests
from requests.packages.urllib3.util.retry import Retry
import requests_kerberos

from cachito.workers.config import get_worker_config

log = logging.getLogger(__name__)


def get_requests_session(auth=False):
    """
    Create a requests session with authentication (when enabled).

    :param bool auth: configure authentication on the session
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

    retry = Retry(
        total=5, read=5, connect=5, backoff_factor=1.3, status_forcelist=(500, 502, 503, 504)
    )
    adapter = requests.adapters.HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


requests_auth_session = get_requests_session(auth=True)
requests_session = get_requests_session()
