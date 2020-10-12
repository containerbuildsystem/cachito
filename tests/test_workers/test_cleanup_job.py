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
            "user": "tom.hanks@domain.local",
        },
    ],
    "meta": {
        "first": (
            "https://cachito.stage.domain.local/api/v1/requests"
            "?page=1&per_page=20&verbose=False&state=complete"
        ),
        "last": (
            "https://cachito.stage.domain.local/api/v1/requests"
            "?page=1&per_page=20&verbose=False&state=complete"
        ),
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
            "user": "tom.hanks@domain.local",
        },
    ],
    "meta": {
        "first": (
            "https://cachito.stage.domain.local/api/v1/requests"
            "?page=1&per_page=20&verbose=False&state=in_progress"
        ),
        "last": (
            "https://cachito.stage.domain.local/api/v1/requests"
            "?page=1&per_page=20&verbose=False&state=in_progress"
        ),
        "next": None,
        "page": 1,
        "pages": 1,
        "per_page": 20,
        "previous": None,
        "total": 1,
    },
}

mock_failed = {
    "items": [
        {
            "dependencies": 309,
            "environment_variables": {},
            "flags": [],
            "id": 52,
            "pkg_managers": ["gomod"],
            "ref": "a7ac8d4c0b7fe90d51fb911511cbf6939655c877",
            "repo": "https://github.com/kubernetes/kubernetes.git",
            "state": "failed",
            "state_reason": "The request failed",
            "updated": "2019-09-05T18:24:50.857861",
            "user": "tom.hanks@domain.local",
        },
    ],
    "meta": {
        "first": "https://cachito.domain.local/api/v1/requests"
        "?page=1&per_page=20&verbose=False&state=failed",
        "last": "https://cachito.domain.local/api/v1/requests"
        "?page=1&per_page=20&verbose=False&state=failed",
        "next": None,
        "page": 1,
        "pages": 1,
        "per_page": 20,
        "previous": None,
        "total": 1,
    },
}


class MockRequestsPagination:
    """Mock pagination behaviour of the /requests endpoint."""

    PER_PAGE = 10
    SELF_URL = "http://example.org/api/v1/requests"

    def __init__(self, total_complete_requests):
        """
        Initialize the instance.

        :param int total_complete_requests: total number of requests in complete state
        """
        self.complete_ids = list(range(1, total_complete_requests + 1))
        self.stale_ids = []
        self.page = 1

    @property
    def _current_index(self):
        return (self.page - 1) * self.PER_PAGE

    def get(self, *args, params=None, **kwargs):
        """
        Get one page of requests data.

        If `state` param is anything other than "complete", returns empty response.
        """
        if params != {"state": "complete"}:
            response = mock.Mock(ok=True, json=lambda: {"items": [], "meta": {"next": None}})
            return response

        curr_index = self._current_index
        if curr_index >= len(self.complete_ids):
            raise ValueError(f"Page {self.page} does not exist")

        self.page += 1
        next_index = self._current_index

        request_ids = self.complete_ids[curr_index:next_index]
        json_data = {
            "items": [
                {"id": request_id, "state": "complete", "updated": "1970-01-01T01:00:00"}
                for request_id in request_ids
            ],
            "meta": {"next": self.SELF_URL if next_index < len(self.complete_ids) else None},
        }

        response = mock.Mock(ok=True, json=lambda: json_data)
        return response

    def patch(self, url, *args, **kwargs):
        """Mark a request as stale."""
        request_id = int(url.rsplit("/", 1)[-1])
        self.complete_ids.remove(request_id)
        self.stale_ids.append(request_id)

        response = mock.Mock(ok=True)
        return response


@mock.patch("cachito.workers.config.Config.cachito_request_lifetime", 1)
@mock.patch("cachito.workers.cleanup_job.datetime")
@mock.patch("cachito.workers.cleanup_job.auth_session")
@mock.patch("cachito.workers.cleanup_job.session")
def test_cleanup_job_pagination_behaviour(mock_basic_session, mock_auth_session, mock_dt):
    """Test that marking requests as stale does not mess with pagination."""
    mock_dt.utcnow = mock.Mock(return_value=datetime(2020, 10, 12))
    mock_dt.strptime = mock.Mock(return_value=datetime(2019, 10, 12))

    # 11 requests will be split into two pages (10 on the first page, 1 on the second page)
    mock_paginated_session = MockRequestsPagination(total_complete_requests=11)

    mock_basic_session.get = mock_paginated_session.get
    mock_auth_session.patch = mock_paginated_session.patch

    main()

    # All complete requests should have been marked as stale
    assert mock_paginated_session.complete_ids == []
    assert mock_paginated_session.stale_ids == list(range(1, 12))
    # We should be past the last page (last page is 2, we should be on page 3)
    assert mock_paginated_session.page == 3


@mock.patch("cachito.workers.config.Config.cachito_request_lifetime", 1)
@mock.patch("cachito.workers.cleanup_job.datetime")
@mock.patch("cachito.workers.cleanup_job.auth_session.patch")
@mock.patch("cachito.workers.cleanup_job.session.get")
def test_cleanup_job_success(mock_requests, mock_auth_requests, mock_dt):
    mock_dt.utcnow = mock.Mock(return_value=datetime(2019, 9, 7))
    mock_dt.strptime = mock.Mock(return_value=datetime(2019, 9, 5))
    mock_requests.return_value.json.side_effect = [mock_complete, mock_in_progress, mock_failed]
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
        mock.call(
            "http://cachito.domain.local/api/v1/requests/52",
            json={"state": "stale", "state_reason": "The request has expired"},
            timeout=60,
        ),
    ]
    assert mock_requests.call_count == 3
    assert mock_auth_requests.call_count == 3
    mock_auth_requests.assert_has_calls(calls)


@mock.patch("cachito.workers.config.Config.cachito_request_lifetime", 1)
@mock.patch("cachito.workers.cleanup_job.datetime")
@mock.patch("cachito.workers.cleanup_job.mark_as_stale")
@mock.patch("cachito.workers.cleanup_job.session.get")
def test_cleanup_job_request_not_stale(mock_requests, mock_mark_as_stale, mock_dt):
    mock_dt.utcnow = mock.Mock(return_value=datetime(2019, 9, 5))
    mock_dt.strptime = mock.Mock(return_value=datetime(2019, 9, 5))
    mock_requests.return_value.json.side_effect = [mock_complete, mock_in_progress, mock_failed]
    main()
    assert mock_requests.call_count == 3
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
