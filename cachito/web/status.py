# SPDX-License-Identifier: GPL-3.0-or-later
import time

import flask
import kombu
import requests
import sqlalchemy.exc

from cachito.errors import CachitoError
from cachito.web import db
from cachito.workers.config import get_worker_config
from cachito.workers.tasks.celery import app

ATHENS = "athens"
NEXUS = "nexus"
NEXUS_HOSTER = "nexus-hoster"
DATABASE = "database"
RABBITMQ = "rabbitmq"

PKG_MANAGER_REQUIRES = {
    "gomod": {ATHENS},
    "npm": {NEXUS, NEXUS_HOSTER},
    "pip": {NEXUS, NEXUS_HOSTER},
    "yarn": {NEXUS, NEXUS_HOSTER},
    "*": {DATABASE, RABBITMQ},
}

no_retry_session = requests.Session()
SERVICE_TIMEOUT = 5


def _service_ok(url):
    """
    Check if a service is reachable and working correctly.

    The status of the service is determined from the status code of the response.

    :param str url: url to fetch
    :return: tuple (ok: bool, reason: str or None)
    """
    try:
        resp = no_retry_session.get(url, timeout=SERVICE_TIMEOUT)
    except requests.RequestException:
        return False, "connection failed"

    if not resp.ok:
        return False, f"{resp.status_code}: {resp.reason}"

    return True, None


def nexus_ok(nexus_url):
    """
    Check if Nexus is OK.

    Uses the /status endpoint, specifically /status/writable.

    :param str nexus_url: url of the Nexus host
    :return: tuple (ok: bool, reason: str or None)
    """
    nexus_status_url = f"{nexus_url.rstrip('/')}/service/rest/v1/status/writable"
    return _service_ok(nexus_status_url)


def athens_ok(athens_url):
    """
    Check if Athens is OK.

    Simply checks if the root url is reachable (Athens has no status endpoint).

    :param str athens_url: url of the Athens host
    :return: tuple (ok: bool, reason: str or None)
    """
    return _service_ok(athens_url)


def database_ok():
    """
    Check if the database connection is working.

    :return: tuple (ok: bool, reason: str or None)
    """
    session = db.session()
    try:
        # Does not actually query data, simply returns 1 (if the connection is working)
        session.execute("SELECT 1")
    except sqlalchemy.exc.OperationalError:
        return False, "database connection failed"
    except sqlalchemy.exc.SQLAlchemyError:
        return False, "unknown error occurred"
    finally:
        session.close()

    return True, None


def rabbitmq_ok(broker_url):
    """
    Check if the RabbitMQ connection is working.

    :param str broker_url: url of the AMQP broker (RabbitMQ)
    :return: tuple (ok: bool, reason: str or None)
    """
    with kombu.Connection(broker_url) as connection:
        try:
            connection.ensure_connection(max_retries=0)
        except kombu.exceptions.OperationalError:
            return False, "broker connection failed"
        except kombu.exceptions.KombuError:
            return False, "unknown error occurred"

    return True, None


def _ping_workers(retries):
    """
    Attempt to ping workers, retry on ConnectionError.

    :param int retries: how many times to retry before returning an empty response
    :return: dict of {hostname: reply} as returned by ping()
    """
    inspect = app.control.inspect()

    for i in range(retries + 1):
        if i > 0:
            # 0.25 -> 0.5 -> 1 ...
            time.sleep(0.25 * 2 ** (i - 1))

        try:
            replies = inspect.ping()
        except ConnectionError:
            continue

        return replies or {}

    return {}


def workers_status(retries=2):
    """
    Ping workers, check received replies.

    :param int retries: how many times to retry on ConnectionError when pinging workers
    :return: list of status information for individual workers (empty if no replies)
    """
    replies = _ping_workers(retries=retries)

    # replies is a dict of {hostname: reply}, convert to a sorted list of [(hostname, reply)]
    reply_tuples = sorted(replies.items(), key=lambda kv: kv[0])
    workers = []

    for worker_name, reply in reply_tuples:
        # The reply format does not seem to be documented anywhere, but is
        # likely a dict with one key: "ok"/"error" and a corresponding message.
        worker = {"name": worker_name, "ok": "ok" in reply}

        if "ok" not in reply:
            worker["reason"] = reply.get("error", "unknown reason")

        workers.append(worker)

    return workers


def _can_process(pkg_managers, services, any_worker_ok):
    """
    Check availability for individual package managers.

    :param list pkg_managers: list of package manager names to check
    :param list services: list of status info for individual services
    :param bool any_worker_ok: is any worker available?
    :return dict of {pkg_manager: available (bool)}
    """
    failing_services = set(s["name"] for s in services if not s["ok"])

    if not any_worker_ok or (PKG_MANAGER_REQUIRES["*"] & failing_services):
        pkg_manager_available = {pkg_manager: False for pkg_manager in pkg_managers}
    else:
        pkg_manager_available = {
            pkg_manager: not (PKG_MANAGER_REQUIRES.get(pkg_manager, set()) & failing_services)
            for pkg_manager in pkg_managers
        }

    return pkg_manager_available


def status(*, short=False, worker_ping_retries=2):
    """
    Get status of Cachito workers and services that Cachito depends on.

    :param bool short: raise an error as soon as any problem is found
    :param int worker_ping_retries: how many times to retry on ConnectionError when pinging workers
    :return: dict with the following keys:
        "can_process": dict with availability info for individual package managers
        "services": list of status info for individual services
        "workers": list of status info for individual workers
    :raises CachitoError: if short is True and a problem is found
    """
    services = []

    def add_status(service_name, ok, reason):
        if short and not ok:
            raise CachitoError(f"{service_name} unavailable: {reason}")
        service = {"name": service_name, "ok": ok}
        if not ok:
            service["reason"] = reason
        services.append(service)

    worker_config = get_worker_config()

    add_status(NEXUS, *nexus_ok(worker_config.cachito_nexus_url))
    if worker_config.cachito_nexus_hoster_url:
        add_status(NEXUS_HOSTER, *nexus_ok(worker_config.cachito_nexus_hoster_url))
    add_status(ATHENS, *athens_ok(worker_config.cachito_athens_url))
    add_status(DATABASE, *database_ok())
    rabbit_ok, rabbit_reason = rabbitmq_ok(worker_config.broker_url)
    add_status(RABBITMQ, rabbit_ok, rabbit_reason)

    if rabbit_ok:
        workers = workers_status(retries=worker_ping_retries)
    else:
        workers = []

    any_worker_ok = any(worker["ok"] for worker in workers)
    if short and not any_worker_ok:
        raise CachitoError("no workers are available")

    pkg_managers = flask.current_app.config["CACHITO_PACKAGE_MANAGERS"]

    return {
        "can_process": _can_process(pkg_managers, services, any_worker_ok),
        "services": services,
        "workers": workers,
    }
