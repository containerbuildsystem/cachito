#!python

# Fetch Failed Cachito Jobs.
#
# This script is intended to be run from a Jenkins job.  Server name and date ranges
# (or --yesterday) need to be provided on the cmd line.  A prometheus-style
# prometheus_metrics.txt file is generated, and logs are downloaded into a logs/ subdirectory
#

# example usage - fetch-failed-request-logs.py --yesterday --server my.cachito.server.example.com
#

import argparse
import datetime
import json
import os
import sys
import urllib.parse
from datetime import date, timedelta
from textwrap import dedent

import urllib3
from icecream import ic

# Cachito API Path.
API_PATH = "api/v1"

# Failed state
STATE = "failed"

# Disable all debug statements for prod.
ic.enable()


def _parse_cli():
    parser = argparse.ArgumentParser(description="Fetch failed Cachito jobs in a given time range",
                                     epilog="Either --date OR --yesterday is required.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--date",
        dest="date",
        help="Date to examine for failed Cachito requests, in Year-Mon-Date format.  "
        + "Example: 2021-11-16",
    )
    group.add_argument(
        "--yesterday",
        action="store_true",
        help="Return failures from yesterday.  Causes script to ignore the --date argument",
    )
    parser.add_argument("--server", dest="cachitoserver", help="Cachito Server Hostname",
                        required=True)
    return parser.parse_args()


def _fetch_cachito_jobs(cachitoserver, request_date, state):
    # Cachito response for requests is reverse chronological order, so newest fist.

    # API args
    #
    # -  page=int
    # -  per_page=int
    # -  state=string
    # -  created_from=datetime
    # -  created_to=datetime
    #

    # Cachito config currently has hard limit of 100.
    per_page_requests = 100
    page = 1
    http = urllib3.PoolManager()
    cachito_failed_requests = []
    cachito_total_requests = []
    api_url = "http://" + cachitoserver + "/" + API_PATH

    # Initial URL.  The meta section of the response gives the 'next' URL with
    # an updated page value, and 'None' when at the end of the available
    # requests based on the query.  Using the 'next' URL to simplify the logic here.

    params = urllib.parse.urlencode({
        'per_page': per_page_requests,
        'page': page,
        'created_from': request_date,
        'created_to': request_date,
    })
    request_url = (
        api_url
        + "/requests"
        + "?"
        + params
    )
    while True:
        r = http.request("GET", ic(request_url))
        json_response = json.loads(r.data)
        ic(json_response)

        cachito_total_requests.extend(
            [item for item in json_response["items"]]
        )
        cachito_failed_requests.extend(
            [
                item
                for item in json_response["items"]
                if item["state"] == state
            ]
        )
        if json_response["meta"]["next"] is None:
            ic("Received None for next URL... breaking")
            break
        params = urllib.parse.urlencode({
            'created_from': request_date,
            'created_to': request_date,
        })
        request_url = (
            json_response["meta"]["next"] + "&" + params
        )
    ic(cachito_total_requests)
    ic(cachito_failed_requests)

    return (
        cachito_failed_requests,
        len(cachito_failed_requests),
        len(cachito_total_requests),
    )


def _fetch_cachito_logs(cachitoserver, id):
    #
    # Fetch logs for a specified request.
    #
    ic("Fetching logs for id " + str(id))
    http = urllib3.PoolManager()
    api_url = "https://" + cachitoserver + "/" + API_PATH
    r = http.request("GET", f"{api_url}/requests/{id}/logs")
    ic(str(r.data.decode("utf-8")))

    os.makedirs("logs", exist_ok=True)

    f = open(f"logs/cachito-log-{id}.txt", "w")
    f.write(str(r.data.decode("utf-8")))
    f.close()


def main() -> int:
    """
    cachito-fail.py - Fetch all failed Cachito requests for the day specfied.

    :return:
        Exit 0 on success
    """
    args = _parse_cli()
    ic(args)

    if args.yesterday is False and args.date is None:
        sys.exit("Either a --date or --yesterday argument is required...")

    if args.yesterday:
        request_date = date.today() - timedelta(days=1)
    else:
        request_date = datetime.date.fromisoformat(args.date)

    if (date.today() - timedelta(days=7)) > request_date:
        print("Logs are not available for jobs more than 7 days old")
        sys.exit()

    ic(request_date)

    cachitoserver = args.cachitoserver

    # Fetch failed jobs,  in the date range
    failed_jobs, num_failed_requests, num_total_requests = _fetch_cachito_jobs(
        cachitoserver, request_date, STATE
    )

    # Fetch logs for jobs
    for id in [job["id"] for job in failed_jobs]:
        _fetch_cachito_logs(cachitoserver, id)

    # Create Prometheus Metrics.
    result_file = "prometheus_metrics.txt"
    with open(result_file, "w+") as f:
        f.write(
            dedent(
                f"""\
                # TYPE cachito_requests summary
                # UNIT cachito_requests rate
                # HELP cachito_requests Requests per 24 hour period
                cachito_requests{{label="failed"}} {str(num_failed_requests)}
                cachito_requests{{label="total"}} {str(num_total_requests)}
                """
            )
        )


if __name__ == "__main__":
    sys.exit(main())
