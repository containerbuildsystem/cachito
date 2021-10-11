# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import datetime

from . import utils


def test_get_request_metrics(api_client):
    finished_from = datetime.utcnow().isoformat()
    env_data = utils.load_test_data("pip_packages.yaml")["without_deps"]
    request = api_client.create_new_request(
        payload={
            "repo": env_data["repo"],
            "ref": env_data["ref"],
            "pkg_managers": env_data["pkg_managers"],
        },
    )
    api_client.wait_for_complete_request(request)

    resp = api_client.fetch_request_metrics(finished_from=finished_from)
    assert resp.status_code == 200
    request_metrics = resp.json()["items"][0]
    assert request_metrics["id"] == request.id
    assert request_metrics["duration"] > 0
    assert request_metrics["time_in_queue"] > 0


def test_get_request_metrics_summary(api_client):
    finished_from = datetime.utcnow().isoformat()
    env_data = utils.load_test_data("pip_packages.yaml")["without_deps"]
    total = 3
    for _ in range(total):
        request = api_client.create_new_request(
            payload={
                "repo": env_data["repo"],
                "ref": env_data["ref"],
                "pkg_managers": env_data["pkg_managers"],
            },
        )
        api_client.wait_for_complete_request(request)

    resp = api_client.fetch_request_metrics_summary(
        finished_from=finished_from, finished_to=datetime.utcnow().isoformat(),
    )
    assert resp.status_code == 200
    summary = resp.json()
    assert not {
        "duration_avg",
        "duration_50",
        "duration_95",
        "time_in_queue_avg",
        "time_in_queue_95",
        "total",
    }.difference(summary)
    assert summary["total"] == total
    assert 0 < summary["duration_50"] <= summary["duration_95"]
    assert summary["duration_avg"] > 0
    assert summary["time_in_queue_avg"] > 0
    assert summary["time_in_queue_95"] > 0
