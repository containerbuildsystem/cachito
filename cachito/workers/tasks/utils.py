# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import functools
import logging
from pathlib import Path
from typing import Union, Callable

import requests

from cachito.errors import ValidationError, CachitoError
from cachito.workers.celery_logging import get_function_arg_value
from cachito.workers.config import get_worker_config

__all__ = ["make_base64_config_file", "AssertPackageFiles"]

log = logging.getLogger(__name__)


def make_base64_config_file(content: str, dest_relpath: Union[str, Path]) -> dict:
    """
    Make a dict to be added as a base64-encoded config file to a request.

    :param str content: content of config file
    :param (str | Path) dest_relpath: relative path to config file from root of bundle directory
    :return: dict with "content", "path" and "type" keys
    """
    return {
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "path": str(dest_relpath),
        "type": "base64",
    }


class AssertPackageFiles:
    """Verify the presence (or absence) of files before processing a package manager."""

    def __init__(self, pkg_manager: str, repo_root: Union[str, Path], package_path: str = "."):
        """
        Initialize an instance.

        :param str pkg_manager: the package manager this instance is for, used in error messages
        :param (str | Path) repo_root: the absolute path to the root of a cloned repository
        :param str package_path: optional relative path from the repo root to a package directory
        """
        self._pkg_manager = pkg_manager
        self._root_dir = Path(repo_root)
        self._pkg_dir = self._root_dir / package_path

    def present(self, path: str):
        """
        Check that file path exists and is a file.

        :param str path: relative path to file/dir in package
        :raise ValidationError: if file path does not exist or is not a file
        """
        self._assert(Path.exists, path, True, "the {relpath} file must be present")
        self._assert(Path.is_file, path, True, "{relpath} must be a file")

    def dir_present(self, path: str):
        """
        Check that file path exists and is a directory.

        :param str path: relative path to file/dir in package
        :raise ValidationError: if file path does not exist or is not a directory
        """
        self._assert(Path.exists, path, True, "the {relpath} directory must be present")
        self._assert(Path.is_dir, path, True, "{relpath} must be a directory")

    def absent(self, path: str):
        """
        Check that file path does not exist or is not a file.

        :param str path: relative path to file/dir in package
        :raise ValidationError: if file path exists and is a file
        """
        self._assert(Path.is_file, path, False, "the {relpath} file must not be present")

    def dir_absent(self, path: str):
        """
        Check that file path does not exist or is not a directory.

        :param str path: relative path to file/dir in package
        :raise ValidationError: if file path exists and is a directory
        """
        self._assert(Path.is_dir, path, False, "the {relpath} directory must not be present")

    def _assert(
        self, check_presence: Callable[[Path], bool], path: str, expect: bool, err_template: str
    ):
        """
        Make an assertion about the presence of a file, raise an error if it fails.

        Turns `path` into an absolute path, calls check_presence() on it and compares the result
        with the expected value.

        :param (Path) -> bool check_presence: method to check file presence, e.g. Path.is_file
        :param str path: relative path to file/directory from root of package directory
        :param bool expect: expect the file/directory to be present?
        :param str err_template: error message which may contain {relpath} as a placeholder
        :raises ValidationError: if the assertion fails
        """
        fullpath = self._pkg_dir / path

        if check_presence(fullpath) != expect:
            relpath = fullpath.relative_to(self._root_dir)
            err_msg = err_template.format(relpath=relpath)
            raise ValidationError(f"File check failed for {self._pkg_manager}: {err_msg}")


def runs_if_request_in_progress(task_fn):
    """
    Decorate a task to make it check request state before proceeding.

    If request state is not "in_progress", the task will log an error and exit.

    :param task task_fn: the task to run if its state is in_progress
    """

    @functools.wraps(task_fn)
    def task_with_state_check(*args, **kwargs):
        """Check request state, proceed to execute task if check succeeds."""
        request_id = get_function_arg_value("request_id", task_fn, args, kwargs)
        if not request_id:
            raise ValueError(
                f"Failed during state check: no request_id found for {task_fn.__name__} task",
            )
        request_state = get_request_state(request_id)
        if request_state != "in_progress":
            log.error(
                "Skipping %s task because the request was not in_progress (was: %s)",
                task_fn.__name__,
                request_state,
            )
            return

        return task_fn(*args, **kwargs)

    return task_with_state_check


def get_request(request_id: int) -> dict:
    """
    Download the JSON representation of a request from the Cachito API.

    :param request_id: the Cachito request ID this is for
    :return: JSON representation of the request
    :raises CachitoError: if the connection fails or the API returns an error response
    """
    log.debug("Getting request %d", request_id)
    request = _get_request_or_fail(
        request_id,
        connect_error_msg=f"The connection failed while getting request {request_id}: {{exc}}",
        status_error_msg=f"Failed to get request {request_id}: {{exc}}",
    )
    return request


def get_request_state(request_id):
    """
    Get the state of the request.

    :param int request_id: the Cachito request ID this is for
    """
    log.debug("Getting the state of request %d", request_id)
    request = _get_request_or_fail(
        request_id,
        connect_error_msg=(
            f"The connection failed while getting the state of request {request_id}: {{exc}}"
        ),
        status_error_msg=f"Failed to get the state of request {request_id}: {{exc}}",
    )
    return request["state"]


def _get_request_or_fail(request_id: int, connect_error_msg: str, status_error_msg: str) -> dict:
    """
    Try to download the JSON data for a request from the Cachito API.

    Both error messages can contain the {exc} placeholder which will be replaced by the actual
    exception.

    :param request_id: ID of the request to get
    :param connect_error_msg: error message to raise if the connection fails
    :param status_error_msg: error message to raise if the response status is 4xx or 5xx
    :raises CachitoError: if the connection fails or the API returns an error response
    """
    # Import this here to avoid a circular import (tasks -> requests -> tasks)
    from cachito.workers.requests import requests_session

    config = get_worker_config()
    request_url = f'{config.cachito_api_url.rstrip("/")}/requests/{request_id}'

    try:
        rv = requests_session.get(request_url, timeout=config.cachito_api_timeout)
        rv.raise_for_status()
    except requests.HTTPError as e:
        msg = status_error_msg.format(exc=e)
        log.exception(msg)
        raise CachitoError(msg)
    except requests.RequestException as e:
        msg = connect_error_msg.format(exc=e)
        log.exception(msg)
        raise CachitoError(msg)

    return rv.json()
