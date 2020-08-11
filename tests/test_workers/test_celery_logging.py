# SPDX-License-Identifier: GPL-3.0-or-later
import logging
from unittest import mock

from celery.utils.log import ColorFormatter
import pytest

from cachito.errors import CachitoError
from cachito.workers import celery_logging


def test_cleanup_task_logging(tmp_path):
    # Add a file handler first to test remove handler function
    request_log_handler = logging.FileHandler(tmp_path / "fake-path")
    logger = logging.getLogger()
    logger.addHandler(request_log_handler)

    try:
        celery_logging.cleanup_task_logging(mock.Mock(), mock.Mock())
        for handler in logger.handlers:
            assert not isinstance(handler, logging.FileHandler)

    finally:
        for handler in logger.handlers:
            if isinstance(handler, logging.FileHandler):
                logger.removeHandler(handler)


def test_cleanup_task_logging_customization():
    root_logger = logging.getLogger()
    original_formatters = []
    for handler in root_logger.handlers:
        original_formatters.append(handler.formatter)

    color_handler = logging.StreamHandler()
    color_handler.setFormatter(ColorFormatter("%(message)s"))
    log_filter = celery_logging.AddRequestIDFilter(5)
    color_handler.addFilter(log_filter)
    root_logger.addHandler(color_handler)

    try:
        celery_logging.cleanup_task_logging_customization(mock.Mock(), mock.Mock())
    finally:
        root_logger.removeHandler(color_handler)

        for handler, formatter in zip(root_logger.handlers, original_formatters):
            handler.setFormatter(formatter)

    # Verify that the AddRequestIDFilter filter was removed from the color handler
    assert len(color_handler.filters) == 0


@mock.patch("cachito.workers.celery_logging.get_worker_config")
def test_setup_logging(mock_gwc, tmpdir):
    mock_gwc.return_value.cachito_request_file_logs_dir = str(tmpdir)
    mock_gwc.return_value.cachito_request_file_logs_level = "DEBUG"
    mock_gwc.return_value.cachito_request_file_logs_format = (
        "[%(asctime)s %(name)s %(levelname)s %(module)s.%(funcName)s] %(message)s"
    )
    mock_gwc.return_value.cachito_request_file_logs_perm = 0o660
    workers_logger = logging.getLogger("cachito.workers")
    workers_logger.disabled = False
    workers_logger.setLevel(logging.INFO)

    logger = logging.getLogger()

    task_id = mock.Mock()
    task = mock.Mock()

    def _dummy_task(msg, request_id):
        return

    task.__wrapped__ = _dummy_task

    try:
        celery_logging.setup_task_logging(task_id, task, args=["hello"], kwargs={"request_id": 3})
        workers_logger.info("Test log message")
    finally:
        for handler in logger.handlers:
            if isinstance(handler, logging.FileHandler):
                logger.removeHandler(handler)

    # verify that correct message was logged
    with open(tmpdir.join("3.log")) as f:
        assert "Test log message" in f.read()


@mock.patch("cachito.workers.celery_logging._get_function_arg_value")
@mock.patch("cachito.workers.celery_logging.get_worker_config")
def test_setup_logging_request_id_not_found(mock_gwc, mock_get_func_arg_val, tmpdir):
    mock_gwc.return_value.cachito_request_file_logs_dir = str(tmpdir)
    mock_gwc.return_value.cachito_request_file_logs_level = "DEBUG"
    mock_gwc.return_value.cachito_request_file_logs_format = (
        "[%(asctime)s %(name)s %(levelname)s %(module)s.%(funcName)s] %(message)s"
    )
    mock_get_func_arg_val.return_value = None

    task_id = mock.Mock()
    task = mock.Mock()

    def _dummy_task(msg, request_id):
        return

    task.__wrapped__ = _dummy_task

    expected = "Unable to get 'request_id'"
    with pytest.raises(CachitoError, match=expected):
        celery_logging.setup_task_logging(task_id, task, args=["hello"], kwargs={"request_id": 3})


def test_setup_task_logging_customization(caplog):
    # Setting the logging level via caplog.set_level is not sufficient. The Flask
    # related settings from previous tests interfere with this.
    workers_logger = logging.getLogger("cachito.workers")
    workers_logger.disabled = False
    workers_logger.setLevel(logging.INFO)

    root_logger = logging.getLogger()
    original_formatters = []
    for handler in root_logger.handlers:
        original_formatters.append(handler.formatter)

    color_handler = logging.StreamHandler()
    color_handler.setFormatter(ColorFormatter("%(message)s"))
    root_logger.addHandler(color_handler)

    task_id = mock.Mock()
    task = mock.Mock()

    def _dummy_task(msg, request_id):
        return

    task.__wrapped__ = _dummy_task

    try:
        celery_logging.setup_task_logging_customization(
            task_id, task, args=["hello"], kwargs={"request_id": 3}
        )
        workers_logger.info("Test log message")
        assert "#%(request_id)s" in color_handler.formatter._fmt
    finally:
        root_logger.removeHandler(color_handler)

        for handler, formatter in zip(root_logger.handlers, original_formatters):
            handler.setFormatter(formatter)

    # Verify that the request filter and formatter were properly set
    expected = (
        "#3 cachito.workers INFO test_celery_logging.test_setup_task_logging_customization]"
        " Test log message"
    )
    assert expected in caplog.text
