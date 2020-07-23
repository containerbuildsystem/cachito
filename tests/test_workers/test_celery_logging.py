# SPDX-License-Identifier: GPL-3.0-or-later
import logging
from unittest import mock

from celery.utils.log import ColorFormatter

from cachito.workers import celery_logging


def test_setup_task_logging(caplog):
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
        celery_logging.setup_task_logging(task_id, task, args=["hello"], kwargs={"request_id": 3})
        workers_logger.info("Test log message")
        assert "#%(request_id)s" in color_handler.formatter._fmt
    finally:
        root_logger.removeHandler(color_handler)

        for handler, formatter in zip(root_logger.handlers, original_formatters):
            handler.setFormatter(formatter)

    # Verify that the request filter and formatter were properly set
    expected = (
        "#3 cachito.workers INFO test_celery_logging.test_setup_task_logging] Test log message"
    )
    assert expected in caplog.text


def test_cleanup_task_logging():
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
        celery_logging.cleanup_task_logging(mock.Mock(), mock.Mock())
    finally:
        root_logger.removeHandler(color_handler)

        for handler, formatter in zip(root_logger.handlers, original_formatters):
            handler.setFormatter(formatter)

    # Verify that the AddRequestIDFilter filter was removed from the color handler
    assert len(color_handler.filters) == 0
