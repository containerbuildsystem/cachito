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
    """Mark all end of life requests as stale using the REST API."""
    for state in ("complete", "in_progress", "failed"):
        stale_candidate_requests = find_all_requests_in_state(state)
        identify_and_mark_stale_requests(stale_candidate_requests)


def find_all_requests_in_state(state):
    """
    Find all requests in specified state.

    :param str state: state of request, e.g. 'complete', 'in_progress'
    :return: list of Cachito requests (as JSON data) in specified state
    """
    found_requests = []

    url = f"{config.cachito_api_url.rstrip('/')}/requests"
    while url:
        try:
            response = session.get(url, params={"state": state}, timeout=config.cachito_api_timeout)
        except requests.RequestException:
            msg = f"The connection failed when querying {url}"
            log.exception(msg)
            raise CachitoError(msg)

        if not response.ok:
            log.error(
                "The request to %s failed with the status code %d and the following text: %s",
                url,
                response.status_code,
                response.text,
            )
            raise CachitoError(
                "Could not reach the Cachito API to find the requests to be marked as stale"
            )

        json_response = response.json()
        found_requests.extend(json_response["items"])
        url = json_response["meta"]["next"]

    # Remove potential duplicates found due to the dynamic behaviour of pagination
    deduplicated = {request["id"]: request for request in found_requests}
    return list(deduplicated.values())


def identify_and_mark_stale_requests(requests_json):
    """
    Identify Cachito requests which have reached end of life and mark them as stale.

    :param list requests_json: list of Cachito requests (as JSON data)
    """
    current_time = datetime.utcnow()
    for request in requests_json:
        if request["state"] not in ("complete", "in_progress", "failed"):
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
