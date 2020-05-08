from datetime import datetime
from unittest import mock

import pytest
import requests

from cachito.errors import CachitoError
from cachito.workers.cleanup_job import main


mock_complete = {
    "items": [
        {
            "dependencies": 309,
            "environment_variables": {},
            "flags": [],
            "id": 50,
            "pkg_managers": ["gomod"],
            "ref": "a7ac8d4c0b7fe90d51fb911511cbf6939655c877",
            "repo": "https://github.com/kubernetes/kubernetes.git",
            "state": "complete",
            "state_reason": "Completed successfully",
            "updated": "2019-09-05T18:24:50.857861",
            "user": "mprahl@redhat.com",
        },
    ],
    "meta": {
        "first": "https://cachito.stage.engineering.redhat.com/api/v1/requests"
        "?page=1&per_page=20&verbose=False&state=complete",
        "last": "https://cachito.stage.engineering.redhat.com/api/v1/requests"
        "?page=1&per_page=20&verbose=False&state=complete",
        "next": None,
        "page": 1,
        "pages": 1,
        "per_page": 20,
        "previous": None,
        "total": 1,
    },
}


mock_in_progress = {
    "items": [
        {
            "dependencies": 309,
            "environment_variables": {},
            "flags": [],
            "id": 51,
            "pkg_managers": ["gomod"],
            "ref": "a7ac8d4c0b7fe90d51fb911511cbf6939655c877",
            "repo": "https://github.com/kubernetes/kubernetes.git",
            "state": "in_progress",
            "state_reason": "The request was initiated",
            "updated": "2019-09-05T18:24:50.857861",
            "user": "mprahl@redhat.com",
        },
    ],
    "meta": {
        "first": "https://cachito.stage.engineering.redhat.com/api/v1/requests"
        "?page=1&per_page=20&verbose=False&state=in_progress",
        "last": "https://cachito.stage.engineering.redhat.com/api/v1/requests"
        "?page=1&per_page=20&verbose=False&state=in_progress",
        "next": None,
        "page": 1,
        "pages": 1,
        "per_page": 20,
        "previous": None,
        "total": 1,
    },
}


@mock.patch("cachito.workers.config.Config.cachito_request_lifetime", 1)
@mock.patch("cachito.workers.cleanup_job.datetime")
@mock.patch("cachito.workers.cleanup_job.auth_session.patch")
@mock.patch("cachito.workers.cleanup_job.session.get")
def test_cleanup_job_success(mock_requests, mock_auth_requests, mock_dt):
    mock_dt.utcnow = mock.Mock(return_value=datetime(2019, 9, 7))
    mock_dt.strptime = mock.Mock(return_value=datetime(2019, 9, 5))
    mock_requests.return_value.json.side_effect = [mock_complete, mock_in_progress]
    mock_auth_requests.return_value.ok = True
    main()
    calls = [
        mock.call(
            "http://cachito.domain.local/api/v1/requests/50",
            json={"state": "stale", "state_reason": "The request has expired"},
            timeout=60,
        ),
        mock.call(
            "http://cachito.domain.local/api/v1/requests/51",
            json={"state": "stale", "state_reason": "The request has expired"},
            timeout=60,
        ),
    ]
    assert mock_requests.call_count == 2
    assert mock_auth_requests.call_count == 2
    mock_auth_requests.assert_has_calls(calls)


@mock.patch("cachito.workers.config.Config.cachito_request_lifetime", 1)
@mock.patch("cachito.workers.cleanup_job.datetime")
@mock.patch("cachito.workers.cleanup_job.mark_as_stale")
@mock.patch("cachito.workers.cleanup_job.session.get")
def test_cleanup_job_request_not_stale(mock_requests, mock_mark_as_stale, mock_dt):
    mock_dt.utcnow = mock.Mock(return_value=datetime(2019, 9, 5))
    mock_dt.strptime = mock.Mock(return_value=datetime(2019, 9, 5))
    mock_requests.return_value.json.side_effect = [mock_complete, mock_in_progress]
    main()
    assert mock_requests.call_count == 2
    assert not mock_mark_as_stale.called


@mock.patch("cachito.workers.config.Config.cachito_request_lifetime", 1)
@mock.patch("cachito.workers.cleanup_job.session.get")
def test_cleanup_job_request_get_timeout(mock_requests):
    mock_requests.side_effect = requests.ConnectionError()
    expected = "The connection failed when querying .+"
    with pytest.raises(CachitoError, match=expected):
        main()
    assert mock_requests.call_count == 1


@mock.patch("cachito.workers.config.Config.cachito_request_lifetime", 1)
@mock.patch("cachito.workers.cleanup_job.session.get")
def test_cleanup_job_request_failed_get(mock_requests):
    mock_requests.return_value.ok = False
    expected = "Could not reach the Cachito API to find the requests to be marked as stale"
    with pytest.raises(CachitoError, match=expected):
        main()
    assert mock_requests.call_count == 1
