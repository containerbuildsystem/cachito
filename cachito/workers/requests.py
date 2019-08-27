# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os

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
    if auth and config.cachito_auth_type == 'kerberos':
        if config.cachito_kerberos_keytab:
            log.debug(
                'Setting the environment variable KRB5_CLIENT_KTNAME to %r',
                config.cachito_kerberos_keytab,
            )
            os.environ['KRB5_CLIENT_KTNAME'] = config.cachito_kerberos_keytab

        log.debug(
            'Configuring the requests session\'s authentication with the keytab path %r',
            os.environ['KRB5_CLIENT_KTNAME'],
        )
        session.auth = requests_kerberos.HTTPKerberosAuth(
            mutual_authentication=requests_kerberos.OPTIONAL)
    retry = Retry(
        total=3,
        read=3,
        connect=3,
        backoff_factor=1,
        status_forcelist=(500, 502, 503, 504),
    )
    adapter = requests.adapters.HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


requests_auth_session = get_requests_session(auth=True)
requests_session = get_requests_session()
