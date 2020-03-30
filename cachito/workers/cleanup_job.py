#!/usr/bin/env python3
import logging
from datetime import datetime, timedelta

import requests

from cachito.workers.requests import get_requests_session
from cachito.workers.config import get_worker_config
from cachito.errors import CachitoError

log = logging.getLogger(__name__)

auth_session = get_requests_session(auth=True)
session = get_requests_session()
config = get_worker_config()

# state, state_reason and payload values for marking a request in Cachito API
state = "stale"
state_reason = "The request has expired"
payload = {"state": state, "state_reason": state_reason}


def main():
    """Mark all stale completed requests as stale using the REST API."""
    url = config.cachito_api_url.rstrip("/") + "/requests?state=complete"
    while True:
        json_response = get_completed_requests(url)
        identify_and_mark_stale_requests(json_response["items"])
        if json_response["meta"]["next"]:
            url = json_response["meta"]["next"]
        else:
            break


def get_completed_requests(url):
    """
    Get one page of completed requests from the Cachito API.

    :param str url: the URL to fetch the Cachito requests from
    :raise CachitoError: if the request to the Cachito API fails
    :rtype: dict
    """
    response = session.get(url)
    if not response.ok:
        raise CachitoError("Could not reach Cachito API to get all completed requests")
    return response.json()


def identify_and_mark_stale_requests(requests_json):
    """
    Identify completed Cachito requests which have reached end of life and mark them as stale.

    :param dict requests_json: the JSON representation of a Cachito API response page
    """
    current_time = datetime.utcnow()
    for request in requests_json:
        if request["state"] != "complete":
            continue
        date_time_obj = datetime.strptime(request["updated"], "%Y-%m-%dT%H:%M:%S.%f")
        if current_time - date_time_obj > timedelta(config.cachito_request_lifetime):
            mark_as_stale(request["id"])


def mark_as_stale(request_id):
    """
    Mark the identified stale request ID as `stale` in Cachito.

    :param int request_id: request ID identified as stale
    :raise CachitoError: if the request to the Cachito API fails
    """
    try:
        log.info("Setting state of request %d to `stale`", request_id)
        request_rv = auth_session.patch(
            f'{config.cachito_api_url.rstrip("/")}/requests/{request_id}',
            json=payload,
            timeout=config.cachito_api_timeout,
        )
    except requests.RequestException:
        msg = f"The connection failed when setting the `stale` state on request {request_id}"
        log.exception(msg)
        raise CachitoError(msg)

    if not request_rv.ok:
        log.error(
            "Failed to set the `stale` state on request %d. The status was %d. "
            "The text was:\n%s",
            request_id,
            request_rv.status_code,
            request_rv.text,
        )


if __name__ == "__main__":
    log.setLevel(logging.INFO)
    main()
