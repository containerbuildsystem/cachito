# SPDX-License-Identifier: GPL-3.0-or-later
import inspect
import logging

from celery.utils.log import ColorFormatter

from cachito.workers.config import get_worker_config


class AddRequestIDFilter(logging.Filter):
    """A log filter that sets ``request_id`` on the log record."""

    def __init__(self, request_id, *args, **kwargs):
        """
        Initialize a filter that sets ``request_id`` on the log record.

        :param request_id: the request ID to set on the record
        """
        super().__init__(*args, **kwargs)
        self._request_id = request_id

    def filter(self, record):
        """
        Set ``request_id`` on the log record.

        :param logging.LogRecord record: the log record
        :return: always returns ``True`` so the log is not filtered out
        :rtype: bool
        """
        record.request_id = self._request_id
        return True


def _get_function_arg_value(arg_name, func, args, kwargs):
    """
    Get the value of the given argument name.

    :param str arg_name: the name of the argument to get
    :param function func: the function the arguments are for
    :param list args: the list of arguments passed to the function
    :param dict kwargs: the dictionary or keyword arguments passed to the function
    :return: the argument value or ``None``
    """
    original_func = func
    while getattr(original_func, "__wrapped__", None):
        original_func = original_func.__wrapped__
    argspec = inspect.getfullargspec(original_func).args

    arg_index = argspec.index(arg_name)
    arg_value = kwargs.get(arg_name, None)
    if arg_value is None and len(args) > arg_index:
        arg_value = args[arg_index]

    return arg_value


def setup_task_logging(task_id, task, **kwargs):
    """
    Customize the logging for the task.

    This adds a filter that sets ``request_id`` on the log record. If the request ID
    cannot be determined, "unknown" is set instead. This also sets the log format to
    the ``cachito_task_log_format`` config.

    :param str task_id: the task ID
    :param class task: the class of the task being executed
    """
    conf = get_worker_config()

    request_id = _get_function_arg_value("request_id", task, kwargs["args"], kwargs["kwargs"])
    log_filter = AddRequestIDFilter(str(request_id) or "unknown")
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if not isinstance(handler, logging.StreamHandler):
            continue

        handler.addFilter(log_filter)
        if isinstance(handler.formatter, ColorFormatter):
            formatter = ColorFormatter(
                conf.cachito_task_log_format, use_color=handler.formatter.use_color
            )
        else:
            formatter = logging.Formatter(conf.cachito_task_log_format)
        handler.setFormatter(formatter)


def cleanup_task_logging(task_id, task, **kwargs):
    """
    Clean up any logging customizations that were set in ``setup_task_logging``.

    :param str task_id: the task ID
    :param class task: the class of the task being executed
    """
    conf = get_worker_config()

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if not isinstance(handler, logging.StreamHandler):
            continue

        if isinstance(handler.formatter, ColorFormatter):
            formatter = ColorFormatter(
                conf.worker_log_format, use_color=handler.formatter.use_color
            )
        else:
            formatter = logging.Formatter(conf.worker_log_format)
        handler.setFormatter(formatter)

        for log_filter in handler.filters:
            if isinstance(log_filter, AddRequestIDFilter):
                handler.removeFilter(log_filter)
