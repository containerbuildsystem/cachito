# SPDX-License-Identifier: GPL-3.0-or-later
import functools
import json
import os
import tempfile
from collections import OrderedDict
from copy import deepcopy
from datetime import date, datetime
from typing import Any, Dict, Union

import flask
import kombu.exceptions
import pydantic
from celery import chain
from flask import stream_with_context
from flask_login import current_user, login_required
from sqlalchemy import and_, func
from sqlalchemy.orm import joinedload, load_only
from werkzeug.exceptions import BadRequest, Forbidden, Gone, InternalServerError, NotFound

from cachito.common.checksum import hash_file
from cachito.common.packages_data import PackagesData
from cachito.common.paths import RequestBundleDir
from cachito.common.utils import b64encode
from cachito.errors import MessageBrokerError, NoWorkers, RequestErrorOrigin, ValidationError
from cachito.web import db
from cachito.web.content_manifest import BASE_ICM
from cachito.web.metrics import cachito_metrics
from cachito.web.models import (
    ConfigFileBase64,
    EnvironmentVariable,
    PackageManager,
    Request,
    RequestError,
    RequestState,
    RequestStateMapping,
    is_request_ref_valid,
    is_request_repo_valid,
)
from cachito.web.status import status
from cachito.web.utils import deep_sort_icm, normalize_end_date, pagination_metadata, str_to_bool
from cachito.workers import tasks

api_v1 = flask.Blueprint("api_v1", __name__)


class RequestsArgs(pydantic.BaseModel):
    """Query parameters for /request endpoint."""

    created_from: Union[datetime, date, None]
    created_to: Union[datetime, date, None]
    error_origin: Union[RequestErrorOrigin, None]
    error_type: Union[str, None]


@api_v1.route("/status", methods=["GET"])
def get_status():
    """Return status of Cachito workers and services that Cachito depends on."""
    return flask.jsonify(status())


@api_v1.route("/status/short", methods=["GET"])
def get_status_short():
    """Return 200 if all workers and services seem to be ok, 503 otherwise."""
    try:
        status(short=True)
        retval = {"ok": True}
    except NoWorkers as e:
        flask.current_app.logger.error(e)
        retval = {"ok": False, "reason": str(e)}

    return flask.jsonify(retval), 200 if retval["ok"] else 503


@api_v1.route("/requests", methods=["GET"])
def get_requests():
    """
    Retrieve paginated details for requests.

    :rtype: flask.Response
    """
    # Check if the user is filtering requests by state
    state = flask.request.args.get("state")
    # Default verbose flag to False
    verbose = str_to_bool(flask.request.args.get("verbose", False))
    max_per_page = flask.current_app.config["CACHITO_MAX_PER_PAGE"]
    # The call to `paginate` will inspect the current HTTP request for the
    # pagination parameters `page` and `per_page`.
    query = Request.query.order_by(Request.id.desc())
    args = RequestsArgs(**flask.request.args)
    if args.created_from:
        query = query.filter(Request.created >= args.created_from)
    if args.created_to:
        if isinstance(args.created_to, datetime):
            query = query.filter(Request.created <= args.created_to)
        else:
            query = query.filter(
                Request.created <= datetime.combine(args.created_to, datetime.max.time())
            )
    if args.error_origin:
        query = query.filter(and_(Request.error, RequestError.origin == args.error_origin))
    if args.error_type:
        query = query.filter(and_(Request.error, RequestError.error_type == args.error_type))
    if state:
        if state not in RequestStateMapping.get_state_names():
            states = ", ".join(RequestStateMapping.get_state_names())
            raise ValidationError(
                f"{state} is not a valid request state. Valid states are: {states}"
            )
        state_int = RequestStateMapping.__members__[state].value
        query = query.join(RequestState, Request.request_state_id == RequestState.id)
        query = query.filter(RequestState.state == state_int)
    repo = flask.request.args.get("repo")
    if repo:
        if not is_request_repo_valid(repo):
            raise ValidationError('The "repo" parameter must be shorter than 200 characters')
        query = query.filter(Request.repo == repo)
    ref = flask.request.args.get("ref")
    if ref:
        if not is_request_ref_valid(ref):
            raise ValidationError('The "ref" parameter must be a 40 character hex string')
        query = query.filter(Request.ref == ref)
    pkg_managers = flask.request.args.getlist("pkg_manager")
    if pkg_managers:
        pkg_manager_ids = []
        for name in pkg_managers:
            if not name:
                # Ignore if pkg_manager= presents in the querystring
                continue
            pkg_manager: PackageManager = PackageManager.get_by_name(name)
            if pkg_manager is None:
                raise ValidationError(f"Cachito does not have package manager {name}.")
            pkg_manager_ids.append(pkg_manager.id)
        if pkg_manager_ids:
            query = (
                query.join(PackageManager, Request.pkg_managers)
                .filter(PackageManager.id.in_(pkg_manager_ids))
                .group_by(Request.id)
                .having(func.count(PackageManager.id) == len(pkg_manager_ids))
            )
    try:
        per_page = int(flask.request.args.get("per_page", 10))
    except ValueError:
        per_page = 10
    pagination_query = query.paginate(per_page=per_page, max_per_page=max_per_page)
    requests = pagination_query.items
    query_params = {}
    if state:
        query_params["state"] = state
    if verbose:
        query_params["verbose"] = verbose
    response = {
        "items": [request.to_json(verbose=verbose) for request in requests],
        "meta": pagination_metadata(pagination_query, **query_params),
    }
    return flask.jsonify(response)


@api_v1.route("/requests/<int:request_id>", methods=["GET"])
def get_request(request_id):
    """
    Retrieve details for the given request.

    :param int request_id: the value of the request ID
    :return: a Flask JSON response
    :rtype: flask.Response
    :raise NotFound: if the request is not found
    """
    json = Request.query.get_or_404(request_id).to_json()

    if json["state"] == RequestStateMapping.complete.name:
        package_count = len(json["packages"])
        dependency_count = len(json["dependencies"])

        flask.current_app.logger.info(
            "Returning data for request %i. Found %i packages and %i dependencies. "
            "The following package managers were used: %s.",
            request_id,
            package_count,
            dependency_count,
            json["pkg_managers"],
        )

    return flask.jsonify(json)


@api_v1.route("/requests/<int:request_id>/configuration-files", methods=["GET"])
def get_request_config_files(request_id):
    """
    Retrieve the configuration files associated with the given request.

    :param int request_id: the value of the request ID
    :return: a Flask JSON response
    :rtype: flask.Response
    :raise NotFound: if the request is not found
    """
    config_files = Request.query.get_or_404(request_id).config_files_base64
    config_files_json = [config_file.to_json() for config_file in config_files]
    config_files_json = sorted(config_files_json, key=lambda c: c["path"])
    return flask.jsonify(config_files_json)


@api_v1.route("/requests/<int:request_id>/content-manifest", methods=["GET"])
def get_request_content_manifest(request_id):
    """
    Retrieve the content manifest associated with the given request.

    :param int request_id: the value of the request ID
    :return: a Flask JSON response
    :rtype: flask.Response
    :raise NotFound: if the request is not found
    :raise ValidationError: if the request is not in the "complete" or "stale" state
    """
    request = Request.query.get_or_404(request_id)
    if request.state.state_name not in ("complete", "stale"):
        raise ValidationError(
            'Content manifests are only available for requests in the "complete" or "stale" states'
        )
    content_manifest = request.content_manifest
    content_manifest_json = content_manifest.to_json()
    return send_content_manifest_back(content_manifest_json)


@api_v1.route("/requests/<int:request_id>/environment-variables", methods=["GET"])
def get_request_environment_variables(request_id):
    """
    Retrieve the environment variables associated with the given request.

    :param int request_id: the value of the request ID
    :return: a Flask JSON response
    :rtype: flask.Response
    :raise NotFound: if the request is not found
    """
    env_vars = Request.query.get_or_404(request_id).environment_variables
    env_vars_json = OrderedDict()
    for env_var in env_vars:
        env_vars_json[env_var.name] = {"value": env_var.value, "kind": env_var.kind}
    return flask.jsonify(env_vars_json)


@api_v1.route("/requests/<int:request_id>/download", methods=["GET"])
def download_archive(request_id):
    """
    Download archive of source code.

    :param int request_id: the value of the request ID
    :return: a Flask send_file response
    :rtype: flask.Response
    :raise NotFound: if the request is not found
    """
    request = Request.query.get_or_404(request_id)
    if request.state.state_name != "complete":
        raise ValidationError(
            'The request must be in the "complete" state before downloading the archive'
        )

    bundle_dir = RequestBundleDir(request.id, root=flask.current_app.config["CACHITO_BUNDLES_DIR"])

    if not bundle_dir.bundle_archive_file.exists():
        flask.current_app.logger.error(
            "The bundle archive at %s for request %d doesn't exist",
            bundle_dir.bundle_archive_file,
            request_id,
        )
        raise InternalServerError()

    hasher = hash_file(bundle_dir.bundle_archive_file)
    checksum = hasher.hexdigest()
    store_checksum = bundle_dir.bundle_archive_checksum.read_text(encoding="utf-8")
    if checksum != store_checksum:
        msg = "Checksum of bundle archive {} has changed."
        flask.current_app.logger.error(msg.format(bundle_dir.bundle_archive_file))
        raise InternalServerError(msg.format(bundle_dir.bundle_archive_file.name))

    flask.current_app.logger.info(
        "Sending the bundle at %s for request %d", bundle_dir.bundle_archive_file, request_id
    )

    resp = flask.send_file(
        str(bundle_dir.bundle_archive_file),
        mimetype="application/gzip",
        as_attachment=True,
        download_name=f"cachito-{request_id}.tar.gz",
    )
    resp.headers["Digest"] = f"sha-256={b64encode(bytes.fromhex(store_checksum))}"
    return resp


@api_v1.route("/requests/<int:request_id>/packages", methods=["GET"])
def list_packages_and_dependencies(request_id):
    """
    Return the contents of the packages file for a request.

    The primary intent of this endpoint is to allow the packages file verification by the workers.
    All dependencies are also gathered and deduped under a separate key for convenience.

    :rtype: flask.Response
    :raise NotFound: the file is not present. It is a valid state.
    :raise InternalServerError: the file is not present for a completed request. This is an invalid
    state.
    """
    request = Request.query.get_or_404(request_id)

    bundle_dir = RequestBundleDir(request_id, root=flask.current_app.config["CACHITO_BUNDLES_DIR"])

    if not bundle_dir.packages_data.exists():
        message = f"The file at {bundle_dir.packages_data} for request {request_id} doesn't exist."

        if request.state.state_name == RequestStateMapping.complete.name:
            flask.current_app.logger.error(message)
            raise InternalServerError("Invalid state: packages file was not found.")

        flask.current_app.logger.info(message)
        raise NotFound("The packages file is not present for this request.")

    packages_data = PackagesData()
    packages_data.load(bundle_dir.packages_data)

    return {"packages": packages_data.packages, "dependencies": packages_data.all_dependencies}


@api_v1.route("/requests", methods=["POST"])
@login_required
def create_request():
    """
    Submit a request to resolve and cache the given source code and its dependencies.

    :rtype: flask.Response
    :raise ValidationError: if required parameters are not supplied
    :raise MessageBrokerError: if message broker fails to schedule the task to the workers
    """
    payload = flask.request.get_json()
    if not isinstance(payload, dict):
        raise ValidationError("The input data must be a JSON object")

    request = Request.from_json(payload)
    db.session.add(request)
    db.session.commit()

    cachito_metrics["gauge_state"].labels(state="total").inc()
    cachito_metrics["gauge_state"].labels(state=request.state.state_name).inc()

    if current_user.is_authenticated:
        flask.current_app.logger.info(
            "The user %s submitted request %d", current_user.username, request.id
        )
    else:
        flask.current_app.logger.info("An anonymous user submitted request %d", request.id)

    pkg_manager_names = set(pkg_manager.name for pkg_manager in request.pkg_managers)
    supported_pkg_managers = set(flask.current_app.config["CACHITO_PACKAGE_MANAGERS"])
    unsupported_pkg_managers = pkg_manager_names - supported_pkg_managers
    if unsupported_pkg_managers:
        # At this point, unsupported_pkg_managers would only contain valid package managers that
        # are not enabled
        raise ValidationError(
            "The following package managers are not "
            f"enabled: {', '.join(unsupported_pkg_managers)}"
        )

    # Chain tasks
    error_callback = tasks.failed_request_callback.s(request.id)
    chain_tasks = [
        tasks.fetch_app_source.s(
            request.repo,
            request.ref,
            request.id,
            "git-submodule" in pkg_manager_names,
            any(flag.name == "remove-unsafe-symlinks" for flag in request.flags),
        ).on_error(error_callback)
    ]

    pkg_manager_to_dep_replacements = {}
    for dependency_replacement in payload.get("dependency_replacements", []):
        type_ = dependency_replacement["type"]
        pkg_manager_to_dep_replacements.setdefault(type_, [])
        pkg_manager_to_dep_replacements[type_].append(dependency_replacement)

    package_configs = payload.get("packages", {})
    if "gomod" in pkg_manager_names:
        go_package_configs = package_configs.get("gomod", [])
        chain_tasks.append(
            tasks.fetch_gomod_source.si(
                request.id, pkg_manager_to_dep_replacements.get("gomod", []), go_package_configs
            ).on_error(error_callback)
        )
    if "npm" in pkg_manager_names:
        if pkg_manager_to_dep_replacements.get("npm"):
            raise ValidationError(
                "Dependency replacements are not yet supported for the npm package manager"
            )

        npm_package_configs = package_configs.get("npm", [])
        chain_tasks.append(
            tasks.fetch_npm_source.si(request.id, npm_package_configs).on_error(error_callback)
        )
    if "pip" in pkg_manager_names:
        if pkg_manager_to_dep_replacements.get("pip"):
            raise ValidationError(
                "Dependency replacements are not yet supported for the pip package manager"
            )
        pip_package_configs = package_configs.get("pip", [])
        chain_tasks.append(
            tasks.fetch_pip_source.si(request.id, pip_package_configs).on_error(error_callback)
        )
    if "git-submodule" in pkg_manager_names:
        chain_tasks.append(
            tasks.add_git_submodules_as_package.si(request.id).on_error(error_callback)
        )
    if "yarn" in pkg_manager_names:
        if pkg_manager_to_dep_replacements.get("yarn"):
            raise ValidationError(
                "Dependency replacements are not yet supported for the yarn package manager"
            )
        yarn_package_configs = package_configs.get("yarn", [])
        chain_tasks.append(
            tasks.fetch_yarn_source.si(request.id, yarn_package_configs).on_error(error_callback)
        )

    chain_tasks.append(tasks.process_fetched_sources.si(request.id).on_error(error_callback))
    chain_tasks.append(tasks.finalize_request.s(request.id).on_error(error_callback))

    try:
        chain(chain_tasks).delay()
    except kombu.exceptions.OperationalError:
        flask.current_app.logger.exception(
            "Failed to schedule the task for request %d. Failing the request.", request.id
        )
        error = "Failed to schedule the task to the workers. Please try again."
        cachito_metrics["gauge_state"].labels(state=request.state.state_name).dec()
        request.add_state("failed", error)
        cachito_metrics["gauge_state"].labels(state=request.state.state_name).inc()
        db.session.commit()
        raise MessageBrokerError(error)

    flask.current_app.logger.info("Successfully scheduled request %d", request.id)
    return flask.jsonify(request.to_json()), 201


def worker_required(func):
    """
    Decorate a function and assert that the current user is a worker.

    :raise Forbidden: if the user is not a worker
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        allowed_users = flask.current_app.config["CACHITO_WORKER_USERNAMES"]
        # current_user.is_authenticated is only ever False when auth is disabled
        if current_user.is_authenticated and current_user.username not in allowed_users:
            raise Forbidden("This API endpoint is restricted to Cachito workers")
        return func(*args, **kwargs)

    return wrapper


@api_v1.route("/requests/<int:request_id>", methods=["PATCH"])
@login_required
@worker_required
def patch_request(request_id):
    """
    Modify the given request.

    :param int request_id: the request ID from the URL
    :return: a Flask JSON response
    :rtype: flask.Response
    :raise NotFound: if the request is not found
    :raise ValidationError: if the JSON is invalid
    """
    payload = flask.request.get_json()
    if not isinstance(payload, dict):
        raise ValidationError("The input data must be a JSON object")

    if not payload:
        raise ValidationError("At least one key must be specified to update the request")

    valid_keys = {
        "environment_variables",
        "state",
        "state_reason",
        "packages_count",
        "dependencies_count",
        "error_origin",
        "error_type",
    }
    invalid_keys = set(payload.keys()) - valid_keys
    if invalid_keys:
        raise ValidationError(
            "The following keys are not allowed: {}".format(", ".join(invalid_keys))
        )

    for key, value in payload.items():
        if key == "environment_variables":
            if not isinstance(value, dict):
                raise ValidationError('The value for "{}" must be an object'.format(key))
            for env_var_name, env_var_info in value.items():
                EnvironmentVariable.validate_json(env_var_name, env_var_info)
        elif key in ("packages_count", "dependencies_count"):
            if not isinstance(value, int):
                raise ValidationError(f'The value for "{key}" must be an integer')
        elif not isinstance(value, str):
            raise ValidationError('The value for "{}" must be a string'.format(key))

    if "state" in payload and "state_reason" not in payload:
        raise ValidationError('The "state_reason" key is required when "state" is supplied')
    elif "state_reason" in payload and "state" not in payload:
        raise ValidationError('The "state" key is required when "state_reason" is supplied')

    request = Request.query.get_or_404(request_id)
    delete_bundle = False
    delete_bundle_temp = False
    cleanup_nexus = []
    delete_logs = False

    if "state" in payload and "state_reason" in payload:
        cachito_metrics["gauge_state"].labels(state=payload["state"]).inc()
        cachito_metrics["gauge_state"].labels(state=request.state.state_name).dec()
        new_state = payload["state"]
        delete_bundle = new_state == "stale" and request.state.state_name != "failed"
        if new_state in ("stale", "failed"):
            for pkg_manager in ["npm", "pip", "yarn"]:
                if any(p.name == pkg_manager for p in request.pkg_managers):
                    cleanup_nexus.append(pkg_manager)
        delete_bundle_temp = new_state in ("complete", "failed", "stale")
        delete_logs = new_state == "stale"
        new_state_reason = payload["state_reason"]
        # This is to protect against a Celery task getting executed twice and setting the
        # state each time
        if request.state.state_name == new_state and request.state.state_reason == new_state_reason:
            flask.current_app.logger.info("Not adding a new state since it matches the last state")
        else:
            if new_state == "complete":
                cachito_metrics["request_duration"].observe(
                    (datetime.now() - request.created).total_seconds()
                )
            request.add_state(new_state, new_state_reason)

    # If the request fails, a RequestError object will be added to the DB
    if (
        "state" in payload
        and payload["state"] == "failed"
        and "error_origin" in payload
        and "error_type" in payload
    ):
        error_data = {
            "request_id": request_id,
            "origin": payload["error_origin"],
            "error_type": payload["error_type"],
            "message": payload["state_reason"],
        }

        # Delete RequestError if it already exists for the following Request ID
        req_error_query = db.session.query(RequestError)
        req_error_in_db = req_error_query.filter(RequestError.request_id == request_id).first()
        if req_error_in_db:
            db.session.delete(req_error_in_db)

        error_obj = RequestError.from_json(error_data)
        db.session.add(error_obj)

    for env_var_name, env_var_info in payload.get("environment_variables", {}).items():
        env_var_obj = EnvironmentVariable.query.filter_by(name=env_var_name, **env_var_info).first()
        if not env_var_obj:
            env_var_obj = EnvironmentVariable.from_json(env_var_name, env_var_info)
            db.session.add(env_var_obj)

        if env_var_obj not in request.environment_variables:
            request.environment_variables.append(env_var_obj)

    for attr in ("packages_count", "dependencies_count"):
        value = payload.get(attr)
        if value is not None:
            setattr(request, attr, value)

    db.session.commit()

    bundle_dir: RequestBundleDir = RequestBundleDir(
        request.id, root=flask.current_app.config["CACHITO_BUNDLES_DIR"]
    )

    if delete_bundle and bundle_dir.bundle_archive_file.exists():
        flask.current_app.logger.info(
            "Deleting the bundle archive %s", bundle_dir.bundle_archive_file
        )
        try:
            bundle_dir.bundle_archive_file.unlink()
            bundle_dir.bundle_archive_checksum.unlink()
            bundle_dir.packages_data.unlink()
        except OSError:
            flask.current_app.logger.exception(
                "Failed to delete the bundle archive %s", bundle_dir.bundle_archive_file
            )

    if delete_bundle_temp and bundle_dir.exists():
        flask.current_app.logger.info(
            "Deleting the temporary files used to create the bundle at %s", bundle_dir
        )
        try:
            bundle_dir.rmtree()
        except OSError:
            flask.current_app.logger.exception(
                "Failed to delete the temporary files (OSError) at %s", bundle_dir
            )
        except Exception as ex:
            flask.current_app.logger.exception(
                "Failed to delete the temporary files (%s) at %s", type(ex).__name__, bundle_dir
            )

    if delete_logs:
        request_log_dir = flask.current_app.config["CACHITO_REQUEST_FILE_LOGS_DIR"]
        path_to_file = os.path.join(request_log_dir, f"{request_id}.log")
        try:
            os.remove(path_to_file)
        except OSError:
            flask.current_app.logger.exception("Failed to delete the log file %s", path_to_file)

    for pkg_mgr in cleanup_nexus:
        flask.current_app.logger.info(
            "Cleaning up the Nexus %s content for request %d", pkg_mgr, request_id
        )
        cleanup_task = getattr(tasks, f"cleanup_{pkg_mgr}_request")
        try:
            cleanup_task.delay(request_id)
        except kombu.exceptions.OperationalError:
            flask.current_app.logger.exception(
                "Failed to schedule the cleanup_%s_request task for request %d. An administrator "
                "must clean this up manually.",
                pkg_mgr,
                request.id,
            )

    if current_user.is_authenticated:
        flask.current_app.logger.info(
            "The user %s patched request %d", current_user.username, request.id
        )
    else:
        flask.current_app.logger.info("An anonymous user patched request %d", request.id)

    return "", 200


@api_v1.route("/requests/<int:request_id>/configuration-files", methods=["POST"])
@login_required
@worker_required
def add_request_config_files(request_id):
    """
    Add the configuration files associated with the given request.

    :param int request_id: the value of the request ID
    :return: a Flask JSON response
    :rtype: flask.Response
    :raise NotFound: if the request is not found
    :raise ValidationError: if the JSON is invalid
    """
    payload = flask.request.get_json()
    if not isinstance(payload, list):
        raise ValidationError("The input data must be a JSON array")

    request = Request.query.get_or_404(request_id)
    flask.current_app.logger.info(
        "Adding %d configuration files to the request %d", len(payload), request.id
    )

    for config_file in payload:
        ConfigFileBase64.validate_json(config_file)
        config_file_obj = ConfigFileBase64.get_or_create(
            config_file["path"], config_file["content"]
        )
        if config_file_obj not in request.config_files_base64:
            request.config_files_base64.append(config_file_obj)

    if current_user.is_authenticated:
        flask.current_app.logger.info(
            "The user %s added %d configuration files to request %d",
            current_user.username,
            len(payload),
            request.id,
        )
    else:
        flask.current_app.logger.info(
            "An anonymous user added %d configuration files to request %d", len(payload), request.id
        )

    db.session.commit()
    return "", 204


def generate_stream_response(text_file_path):
    """
    Generate response by streaming the content.

    :param str text_file_path: file path to read content from
    :return: streamed content for the given file
    :rtype: Generator[str]
    """
    with open(text_file_path) as f:
        while True:
            data = f.read(1024)
            if not data:
                break
            yield data


@api_v1.route("/requests/<int:request_id>/logs")
def get_request_logs(request_id):
    """
    Retrieve the logs for the Cachito request.

    :param int request_id: the value of the request ID
    :return: a Flask JSON response
    :rtype: flask.Response
    :raise NotFound: if the request is not found
    :raise Gone: if the logs no longer exist
    """
    request_log_dir = flask.current_app.config["CACHITO_REQUEST_FILE_LOGS_DIR"]
    if not request_log_dir:
        raise NotFound()
    request = Request.query.get_or_404(request_id)
    log_file_path = os.path.join(request_log_dir, f"{request_id}.log")
    if not os.path.exists(log_file_path):
        if request.state.state_name == "stale":
            raise Gone(f"The logs for the Cachito request {request_id} no longer exist")
        finalized = request.state.state_name in RequestStateMapping.get_final_states()
        if finalized:
            raise NotFound()
        # The request may not have been initiated yet. Return empty logs until it's processed.
        return flask.Response("", mimetype="text/plain")

    return flask.Response(
        stream_with_context(generate_stream_response(log_file_path)), mimetype="text/plain"
    )


def send_content_manifest_back(content_manifest: Dict[str, Any]) -> flask.Response:
    """Send content manifest back to the client."""
    debug = flask.current_app.logger.debug
    fd, filename = tempfile.mkstemp(prefix="request-content-manifest-json-", text=True)
    debug("Write content manifest into file: %s", filename)
    try:
        with open(fd, "w") as f:
            json.dump(content_manifest, f, sort_keys=True)
        return flask.send_file(filename, mimetype="application/json")
    finally:
        debug("The content manifest is sent back to the client. Remove %s", filename)
        os.unlink(filename)


@api_v1.route("/content-manifest", methods=["GET"])
def get_content_manifest_by_requests():
    """
    Retrieve the content manifest associated with the given requests.

    :return: a Flask JSON response
    :rtype: flask.Response
    :raise BadRequest: if any of the given request is not in the "complete" or
        "stale" state, If any of the given request cannot be found.
    """
    arg = flask.request.args.get("requests")
    if not arg:
        return flask.jsonify(BASE_ICM)
    request_ids = set()
    item: str
    for item in arg.split(","):
        if not item.strip():
            continue
        if not item.strip().isdigit():
            raise BadRequest(f"{item} is not an integer.")
        request_ids.add(int(item))

    requests = (
        Request.query.filter(Request.id.in_(request_ids))
        .options(load_only("id"), joinedload(Request.state))
        .all()
    )
    states = (RequestStateMapping.complete.name, RequestStateMapping.stale.name)
    request: Request
    for request in requests:
        if request.state.state_name not in states:
            raise BadRequest(
                f"Request {request.id} is in state {request.state.state_name}, "
                f"not complete or stale."
            )
        request_ids.remove(request.id)

    if request_ids:
        nonexistent_ids = ",".join(map(str, request_ids))
        raise BadRequest(f"Cannot find request(s) {nonexistent_ids}.")

    assembled_icm = deepcopy(BASE_ICM)
    for request in requests:
        manifest = request.content_manifest.to_json()
        assembled_icm["image_contents"].extend(manifest["image_contents"])
    if len(requests) > 1:
        deep_sort_icm(assembled_icm)
    return send_content_manifest_back(assembled_icm)


class RequestMetricsArgs(pydantic.BaseModel):
    """Query parameters for /request-metrics endpoint."""

    finished_from: Union[datetime, date, None]
    finished_to: Union[datetime, date, None]
    error_origin: Union[RequestErrorOrigin, None]
    error_type: Union[str, None]

    _normalize_end_date = pydantic.validator("finished_to", allow_reuse=True)(normalize_end_date)


@api_v1.route("/request-metrics", methods=["GET"])
def get_request_metrics():
    """Return a list of completed requests with a final state information."""
    max_per_page = flask.current_app.config["CACHITO_MAX_PER_PAGE"]
    args = RequestMetricsArgs(**flask.request.args)
    query = RequestState.get_final_states_query().order_by(RequestState.request_id.desc())
    if args.finished_from:
        query = query.filter(RequestState.updated >= args.finished_from)
    if args.finished_to:
        query = query.filter(RequestState.updated <= args.finished_to)
    if args.error_origin:
        query = query.filter(
            and_(RequestState.request, Request.error, RequestError.origin == args.error_origin)
        )
    if args.error_type:
        query = query.filter(
            and_(RequestState.request, Request.error, RequestError.error_type == args.error_type)
        )

    pagination_query = query.paginate(max_per_page=max_per_page)
    return flask.jsonify(
        {
            "items": [
                {
                    "id": state.request_id,
                    "final_state": RequestStateMapping(state.state).name,
                    "final_state_reason": state.state_reason,
                    "finished": state.updated.isoformat(),
                    "duration": state.duration,
                    "time_in_queue": state.time_in_queue,
                }
                for state in pagination_query.items
            ],
            "meta": pagination_metadata(pagination_query),
        }
    )


class RequestMetricsSummaryArgs(pydantic.BaseModel):
    """Query parameters for /request-metrics/summary endpoint."""

    finished_from: Union[datetime, date]
    finished_to: Union[datetime, date]

    _normalize_end_date = pydantic.validator("finished_to", allow_reuse=True)(normalize_end_date)


@api_v1.route("/request-metrics/summary", methods=["GET"])
def get_request_metrics_summary():
    """Return a summary about completed requests for a given period of time."""
    args = RequestMetricsSummaryArgs(**flask.request.args)
    query = (
        RequestState.get_final_states_query()
        .filter(RequestState.updated >= args.finished_from)
        .filter(RequestState.updated <= args.finished_to)
    )

    client_errors = query.filter(
        and_(RequestState.request, Request.error, RequestError.origin == RequestErrorOrigin.client)
    ).count()
    server_errors = query.filter(
        and_(RequestState.request, Request.error, RequestError.origin == RequestErrorOrigin.server)
    ).count()

    requests = query.subquery()

    states_summary = dict.fromkeys(["complete", "failed"], 0)
    states_summary.update(
        {
            RequestStateMapping(state.state).name: state.requests
            for state in db.session.query(
                requests.c.state, func.count(requests.c.request_id).label("requests")
            ).group_by(requests.c.state)
        }
    )

    (
        duration_avg,
        duration_50,
        duration_95,
        time_in_queue_avg,
        time_in_queue_95,
        total_requests,
    ) = db.session.query(
        func.avg(requests.c.duration),
        func.percentile_cont(0.5).within_group(requests.c.duration),
        func.percentile_cont(0.95).within_group(requests.c.duration),
        func.avg(requests.c.time_in_queue),
        func.percentile_cont(0.95).within_group(requests.c.time_in_queue),
        func.count(requests.c.request_id),
    ).one()

    return flask.jsonify(
        {
            "duration_avg": duration_avg,
            "duration_50": duration_50,
            "duration_95": duration_95,
            "time_in_queue_avg": time_in_queue_avg,
            "time_in_queue_95": time_in_queue_95,
            "client_errors": client_errors,
            "server_errors": server_errors,
            "total": total_requests,
            **states_summary,
        }
    )
