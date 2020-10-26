# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

import kombu
import pytest
import sqlalchemy.exc
from requests import RequestException

from cachito.errors import CachitoError
from cachito.web import status

TEST_PACKAGE_MANAGERS = ["gomod", "npm", "pip", "git-submodule"]


@pytest.fixture
def test_app(app):
    app.config["CACHITO_PACKAGE_MANAGERS"] = TEST_PACKAGE_MANAGERS
    return app


def mock_worker_config(nexus_hoster=False):
    config = mock.Mock()
    config.cachito_nexus_url = "http://nexus:8081"
    config.cachito_nexus_hoster_url = "http://nexus.example.org" if nexus_hoster else None
    config.cachito_athens_url = "http://athens:3000"
    config.broker_url = "amqp://test@rabbitmq:5672//"
    return config


@pytest.mark.parametrize("fail_reason", [None, "connection_error", "status_code"])
@mock.patch.object(status.no_retry_session, "get")
def test_service_ok(mock_requests_get, fail_reason):
    url = "http://username:password@example.org"

    if fail_reason == "connection_error":
        mock_requests_get.side_effect = [RequestException(fail_reason)]
        expect_reason = "connection failed"
    elif fail_reason == "status_code":
        response = mock_requests_get.return_value
        response.ok = False
        response.status_code = 503
        response.reason = "Service Unavailable"
        response.url = url
        expect_reason = "503: Service Unavailable"
    else:
        expect_reason = None

    ok, reason = status._service_ok(url)

    if fail_reason:
        assert not ok
        assert reason == expect_reason
        assert url not in reason
    else:
        assert ok
        assert reason is None

    mock_requests_get.assert_called_once_with(url)


@mock.patch("cachito.web.status._service_ok")
def test_nexus_ok(mock_service_ok):
    nexus_url = "http://nexus:8081/"
    rv = status.nexus_ok(nexus_url)

    assert rv == mock_service_ok.return_value
    mock_service_ok.assert_called_once_with("http://nexus:8081/service/rest/v1/status/writable")


@mock.patch("cachito.web.status._service_ok")
def test_athens_ok(mock_service_ok):
    athens_url = "http://athens:3000/"
    rv = status.athens_ok(athens_url)

    assert rv == mock_service_ok.return_value
    mock_service_ok.assert_called_once_with(athens_url)


@pytest.mark.parametrize(
    "error, expect_reason",
    [
        (None, None),
        (sqlalchemy.exc.SQLAlchemyError("some error"), "unknown error occurred"),
        (
            sqlalchemy.exc.OperationalError("some other error", None, None),
            "database connection failed",
        ),
    ],
)
@mock.patch.object(status.db, "session")
def test_database_ok(mock_db_session, error, expect_reason):
    session = mock_db_session.return_value
    if error is not None:
        session.execute.side_effect = [error]

    ok, reason = status.database_ok()

    assert ok == (error is None)
    assert reason == expect_reason

    session.execute.assert_called_once_with("SELECT 1")
    session.close.assert_called_once()


@pytest.mark.parametrize(
    "error, expect_reason",
    [
        (None, None),
        (kombu.exceptions.KombuError("some error"), "unknown error occurred"),
        (kombu.exceptions.OperationalError("some other error"), "broker connection failed"),
    ],
)
@mock.patch("kombu.Connection")
def test_rabbitmq_ok(mock_kombu_connection, error, expect_reason):
    broker_url = "amqp://test@rabbitmq:5672//"

    connection = mock_kombu_connection.return_value.__enter__.return_value
    if error is not None:
        connection.ensure_connection.side_effect = [error]

    ok, reason = status.rabbitmq_ok(broker_url)

    assert ok == (error is None)
    assert reason == expect_reason

    mock_kombu_connection.assert_called_once_with(broker_url)
    connection.ensure_connection.assert_called_once_with(max_retries=0)


@pytest.mark.parametrize(
    "num_retries, sleep_intervals", [(0, []), (1, [0.25]), (5, [0.25, 0.5, 1.0, 2.0, 4.0])],
)
@mock.patch.object(status.app.control, "inspect")
@mock.patch("time.sleep")
def test_ping_workers_failure(mock_sleep, mock_celery_inspect, num_retries, sleep_intervals):
    failures = [ConnectionResetError("Connection reset by peer") for _ in range(num_retries + 1)]

    ping = mock_celery_inspect.return_value.ping
    ping.side_effect = failures

    assert status._ping_workers(retries=num_retries) == {}

    mock_celery_inspect.assert_called_once()
    assert ping.call_count == num_retries + 1
    assert mock_sleep.call_count == len(sleep_intervals)
    mock_sleep.assert_has_calls(mock.call(t) for t in sleep_intervals)


@pytest.mark.parametrize(
    "max_retries, num_failures, sleep_intervals",
    [(0, 0, []), (1, 0, []), (1, 1, [0.25]), (5, 4, [0.25, 0.5, 1.0, 2.0])],
)
@mock.patch.object(status.app.control, "inspect")
@mock.patch("time.sleep")
def test_ping_workers_success(
    mock_sleep, mock_celery_inspect, max_retries, num_failures, sleep_intervals
):
    failures = [ConnectionResetError("Connection reset by peer") for _ in range(num_failures)]
    success = {"celeery@123456": {"ok": "pong"}}

    ping = mock_celery_inspect.return_value.ping
    ping.side_effect = failures + [success]

    assert status._ping_workers(retries=max_retries) == success

    mock_celery_inspect.assert_called_once()
    assert ping.call_count == num_failures + 1
    assert mock_sleep.call_count == len(sleep_intervals)
    mock_sleep.assert_has_calls(mock.call(t) for t in sleep_intervals)


@pytest.mark.parametrize("retries", [0, 1])
@pytest.mark.parametrize(
    "ping_result, expect_result",
    [
        ({}, []),
        (
            {"celery@abcdef": {"ok": "pong"}, "celery@123456": {"ok": "pong"}},
            # results should be sorted by name
            [{"name": "celery@123456", "ok": True}, {"name": "celery@abcdef", "ok": True}],
        ),
        (
            {"celery@abcdef": {"ok": "pong"}, "celery@123456": {"error": "sucks at ping-pong"}},
            [
                {"name": "celery@123456", "ok": False, "reason": "sucks at ping-pong"},
                {"name": "celery@abcdef", "ok": True},
            ],
        ),
    ],
)
@mock.patch("cachito.web.status._ping_workers")
def test_workers_status(mock_ping_workers, retries, ping_result, expect_result):
    mock_ping_workers.return_value = ping_result

    workers = status.workers_status(retries=retries)
    assert workers == expect_result

    mock_ping_workers.assert_called_once_with(retries=retries)


@pytest.mark.parametrize(
    "worker_ok, failing_services, expect_result",
    [
        (False, [], {"gomod": False, "npm": False, "pip": False, "git-submodule": False}),
        (True, ["rabbitmq"], {"gomod": False, "npm": False, "pip": False, "git-submodule": False}),
        (True, ["database"], {"gomod": False, "npm": False, "pip": False, "git-submodule": False}),
        (True, ["athens"], {"gomod": False, "npm": True, "pip": True, "git-submodule": True}),
        (True, ["nexus"], {"gomod": True, "npm": False, "pip": False, "git-submodule": True}),
        (
            True,
            ["nexus-hoster"],
            {"gomod": True, "npm": False, "pip": False, "git-submodule": True},
        ),
        (
            True,
            ["athens", "nexus"],
            {"gomod": False, "npm": False, "pip": False, "git-submodule": True},
        ),
    ],
)
def test_can_process(worker_ok, failing_services, expect_result):
    services = [{"name": service, "ok": False} for service in failing_services]
    assert status._can_process(TEST_PACKAGE_MANAGERS, services, worker_ok) == expect_result


@pytest.mark.parametrize("short", [True, False])
@pytest.mark.parametrize("nexus_hoster", [True, False])
@mock.patch("cachito.web.status.get_worker_config")
@mock.patch("cachito.web.status.nexus_ok")
@mock.patch("cachito.web.status.athens_ok")
@mock.patch("cachito.web.status.database_ok")
@mock.patch("cachito.web.status.rabbitmq_ok")
@mock.patch("cachito.web.status.workers_status")
@mock.patch("cachito.web.status._can_process")
def test_status_all_ok(
    mock_can_process,
    mock_workers_status,
    mock_rabbitmq_ok,
    mock_database_ok,
    mock_athens_ok,
    mock_nexus_ok,
    mock_get_worker_config,
    nexus_hoster,
    short,
    test_app,
):
    config = mock_worker_config(nexus_hoster=nexus_hoster)

    mock_get_worker_config.return_value = config
    mock_nexus_ok.return_value = (True, None)
    mock_athens_ok.return_value = (True, None)
    mock_database_ok.return_value = (True, None)
    mock_rabbitmq_ok.return_value = (True, None)
    mock_workers_status.return_value = [{"name": "celery@123456", "ok": True}]

    result = status.status(short=short)

    expect_services = [
        {"name": "nexus", "ok": True},
        {"name": "athens", "ok": True},
        {"name": "database", "ok": True},
        {"name": "rabbitmq", "ok": True},
    ]
    if nexus_hoster:
        expect_services.insert(1, {"name": "nexus-hoster", "ok": True})

    assert result == {
        "can_process": mock_can_process.return_value,
        "services": expect_services,
        "workers": mock_workers_status.return_value,
    }

    nexus_ok_calls = [mock.call(config.cachito_nexus_url)]
    if nexus_hoster:
        nexus_ok_calls.append(mock.call(config.cachito_nexus_hoster_url))

    mock_get_worker_config.assert_called_once()
    mock_nexus_ok.assert_has_calls(nexus_ok_calls)
    assert mock_nexus_ok.call_count == len(nexus_ok_calls)
    mock_athens_ok.assert_called_once_with(config.cachito_athens_url)
    mock_database_ok.assert_called_once()
    mock_rabbitmq_ok.assert_called_once_with(config.broker_url)
    mock_workers_status.assert_called_once_with(retries=2)
    mock_can_process.assert_called_once_with(TEST_PACKAGE_MANAGERS, expect_services, True)


@pytest.mark.parametrize("short", [True, False])
@mock.patch("cachito.web.status.get_worker_config")
@mock.patch("cachito.web.status.nexus_ok")
@mock.patch("cachito.web.status.athens_ok")
@mock.patch("cachito.web.status.database_ok")
@mock.patch("cachito.web.status.rabbitmq_ok")
@mock.patch("cachito.web.status.workers_status")
@mock.patch("cachito.web.status._can_process")
def test_status_service_not_ok(
    mock_can_process,
    mock_workers_status,
    mock_rabbitmq_ok,
    mock_database_ok,
    mock_athens_ok,
    mock_nexus_ok,
    mock_get_worker_config,
    short,
    test_app,
):
    config = mock_worker_config()

    mock_get_worker_config.return_value = config
    mock_nexus_ok.return_value = (True, None)
    mock_athens_ok.return_value = (False, "Athens is currently at war with Sparta")
    mock_database_ok.return_value = (True, None)
    mock_rabbitmq_ok.return_value = (True, None)
    mock_workers_status.return_value = [{"name": "celery@123456", "ok": True}]

    if short:
        err_msg = "athens unavailable: Athens is currently at war with Sparta"
        with pytest.raises(CachitoError, match=err_msg):
            status.status(short=True)
        return

    result = status.status(short=False)

    expect_services = [
        {"name": "nexus", "ok": True},
        {"name": "athens", "ok": False, "reason": "Athens is currently at war with Sparta"},
        {"name": "database", "ok": True},
        {"name": "rabbitmq", "ok": True},
    ]

    assert result == {
        "can_process": mock_can_process.return_value,
        "services": expect_services,
        "workers": mock_workers_status.return_value,
    }

    mock_get_worker_config.assert_called_once()
    mock_nexus_ok.assert_called_once_with(config.cachito_nexus_url)
    mock_athens_ok.assert_called_once_with(config.cachito_athens_url)
    assert mock_database_ok.call_count == (0 if short else 1)
    assert mock_rabbitmq_ok.call_count == (0 if short else 1)
    assert mock_workers_status.call_count == (0 if short else 1)
    mock_can_process.assert_called_once_with(TEST_PACKAGE_MANAGERS, expect_services, True)


@pytest.mark.parametrize("short", [True, False])
@pytest.mark.parametrize(
    "workers_status_result, expect_any_worker_ok",
    [
        ([], False),
        ([{"name": "celery@123456", "ok": False, "reason": "is on lunch break"}], False),
        (
            [
                {"name": "celery@123456", "ok": False, "reason": "is on lunch break"},
                {"name": "celery@abcdef", "ok": True},
            ],
            True,
        ),
    ],
)
@mock.patch("cachito.web.status.get_worker_config")
@mock.patch("cachito.web.status.nexus_ok")
@mock.patch("cachito.web.status.athens_ok")
@mock.patch("cachito.web.status.database_ok")
@mock.patch("cachito.web.status.rabbitmq_ok")
@mock.patch("cachito.web.status.workers_status")
@mock.patch("cachito.web.status._can_process")
def test_status_worker_not_ok(
    mock_can_process,
    mock_workers_status,
    mock_rabbitmq_ok,
    mock_database_ok,
    mock_athens_ok,
    mock_nexus_ok,
    mock_get_worker_config,
    short,
    workers_status_result,
    expect_any_worker_ok,
    test_app,
):
    config = mock_worker_config()

    mock_get_worker_config.return_value = config
    mock_nexus_ok.return_value = (True, None)
    mock_athens_ok.return_value = (True, None)
    mock_database_ok.return_value = (True, None)
    mock_rabbitmq_ok.return_value = (True, None)
    mock_workers_status.return_value = workers_status_result

    if short and not expect_any_worker_ok:
        with pytest.raises(CachitoError, match="no workers are available"):
            status.status(short=True)
        return

    result = status.status(short=False)

    expect_services = [
        {"name": "nexus", "ok": True},
        {"name": "athens", "ok": True},
        {"name": "database", "ok": True},
        {"name": "rabbitmq", "ok": True},
    ]

    assert result == {
        "can_process": mock_can_process.return_value,
        "services": expect_services,
        "workers": mock_workers_status.return_value,
    }

    mock_get_worker_config.assert_called_once()
    mock_nexus_ok.assert_called_once_with(config.cachito_nexus_url)
    mock_athens_ok.assert_called_once_with(config.cachito_athens_url)
    mock_database_ok.assert_called_once()
    mock_rabbitmq_ok.assert_called_once_with(config.broker_url)
    mock_workers_status.assert_called_once_with(retries=2)
    mock_can_process.assert_called_once_with(
        TEST_PACKAGE_MANAGERS, expect_services, expect_any_worker_ok
    )


@pytest.mark.parametrize("short", [True, False])
@mock.patch("cachito.web.status.get_worker_config")
@mock.patch("cachito.web.status.nexus_ok")
@mock.patch("cachito.web.status.athens_ok")
@mock.patch("cachito.web.status.database_ok")
@mock.patch("cachito.web.status.rabbitmq_ok")
@mock.patch("cachito.web.status.workers_status")
def test_status_rabbitmq_not_ok(
    mock_workers_status,
    mock_rabbitmq_ok,
    mock_database_ok,
    mock_athens_ok,
    mock_nexus_ok,
    mock_get_worker_config,
    short,
    test_app,
):
    config = mock_worker_config()

    mock_get_worker_config.return_value = config
    mock_nexus_ok.return_value = (True, None)
    mock_athens_ok.return_value = (True, None)
    mock_database_ok.return_value = (True, None)
    mock_rabbitmq_ok.return_value = (False, "failed to resolve broker hostname")

    if short:
        err_msg = "rabbitmq unavailable: failed to resolve broker hostname"
        with pytest.raises(CachitoError, match=err_msg):
            status.status(short=True)
    else:
        result = status.status(short=False)
        assert result["workers"] == []

    # Test that that worker status is not checked if rabbitmq is not ok
    mock_workers_status.assert_not_called()
