# SPDX-License-Identifier: GPL-3.0-or-later
from collections import OrderedDict
import copy
import functools
import os

from celery import chain
import flask
from flask import stream_with_context
from flask_login import current_user, login_required
import kombu.exceptions
from werkzeug.exceptions import Forbidden, InternalServerError, Gone, NotFound

from cachito.errors import CachitoError, ValidationError
from cachito.web import db
from cachito.web.models import (
    ConfigFileBase64,
    Dependency,
    EnvironmentVariable,
    Package,
    Request,
    RequestState,
    RequestStateMapping,
)
from cachito.web.status import status
from cachito.web.utils import pagination_metadata, str_to_bool
from cachito.workers import tasks
from cachito.paths import RequestBundleDir

api_v1 = flask.Blueprint("api_v1", __name__)


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
    except CachitoError as e:
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
    if state:
        if state not in RequestStateMapping.get_state_names():
            states = ", ".join(RequestStateMapping.get_state_names())
            raise ValidationError(
                f"{state} is not a valid request state. Valid states are: {states}"
            )
        state_int = RequestStateMapping.__members__[state].value
        query = query.join(RequestState, Request.request_state_id == RequestState.id)
        query = query.filter(RequestState.state == state_int)
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
    return flask.jsonify(Request.query.get_or_404(request_id).to_json())


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
    return flask.jsonify(content_manifest_json)


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

    flask.current_app.logger.debug(
        "Sending the bundle at %s for request %d", bundle_dir.bundle_archive_file, request_id
    )
    return flask.send_file(
        str(bundle_dir.bundle_archive_file),
        mimetype="application/gzip",
        as_attachment=True,
        attachment_filename=f"cachito-{request_id}.tar.gz",
    )


@api_v1.route("/requests", methods=["POST"])
@login_required
def create_request():
    """
    Submit a request to resolve and cache the given source code and its dependencies.

    :rtype: flask.Response
    :raise ValidationError: if required parameters are not supplied
    """
    payload = flask.request.get_json()
    if not isinstance(payload, dict):
        raise ValidationError("The input data must be a JSON object")

    request = Request.from_json(payload)
    db.session.add(request)
    db.session.commit()

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
            request.repo, request.ref, request.id, "git-submodule" in pkg_manager_names
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

    chain_tasks.append(tasks.create_bundle_archive.si(request.id).on_error(error_callback))

    try:
        chain(chain_tasks).delay()
    except kombu.exceptions.OperationalError:
        flask.current_app.logger.exception(
            "Failed to schedule the task for request %d. Failing the request.", request.id
        )
        error = "Failed to schedule the task to the workers. Please try again."
        request.add_state("failed", error)
        db.session.commit()
        raise CachitoError(error)

    flask.current_app.logger.debug("Successfully scheduled request %d", request.id)
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
        "dependencies",
        "environment_variables",
        "package",
        "package_subpath",
        "state",
        "state_reason",
    }
    invalid_keys = set(payload.keys()) - valid_keys
    if invalid_keys:
        raise ValidationError(
            "The following keys are not allowed: {}".format(", ".join(invalid_keys))
        )

    for key, value in payload.items():
        if key == "dependencies":
            if not isinstance(value, list):
                raise ValidationError('The value for "dependencies" must be an array')
            if "package" not in payload:
                raise ValidationError(
                    'The "package" object must also be provided if the "dependencies" array is '
                    "provided"
                )
            for dep in value:
                Dependency.validate_json(dep, for_update=True)
        elif key == "package":
            Package.validate_json(value)
        elif key == "environment_variables":
            if not isinstance(value, dict):
                raise ValidationError('The value for "{}" must be an object'.format(key))
            for env_var_name, env_var_info in value.items():
                EnvironmentVariable.validate_json(env_var_name, env_var_info)
        elif not isinstance(value, str):
            raise ValidationError('The value for "{}" must be a string'.format(key))

    if "package_subpath" in payload and "package" not in payload:
        raise ValidationError(
            'The "package" object must also be provided if "package_subpath" is provided'
        )

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
        new_state = payload["state"]
        delete_bundle = new_state == "stale" and request.state.state_name != "failed"
        if new_state in ("stale", "failed"):
            for pkg_manager in ["npm", "pip", "yarn"]:
                if any(p.name == pkg_manager for p in request.pkg_managers):
                    cleanup_nexus.append(pkg_manager)
        delete_bundle_temp = new_state in ("complete", "failed")
        delete_logs = new_state == "stale"
        new_state_reason = payload["state_reason"]
        # This is to protect against a Celery task getting executed twice and setting the
        # state each time
        if request.state.state_name == new_state and request.state.state_reason == new_state_reason:
            flask.current_app.logger.info("Not adding a new state since it matches the last state")
        else:
            request.add_state(new_state, new_state_reason)

    package_object = None
    if "package" in payload:
        package_object = Package.get_or_create(payload["package"])

        package_attrs = {}
        # The presence of "package_subpath" in payload indicates whether to modify the subpath.
        # This is only allowed when creating a new package, so when the PATCH API is used to
        # modify an existing package, the user must make sure to use the same subpath (or no
        # subpath).
        if "package_subpath" in payload:
            package_attrs["subpath"] = payload["package_subpath"]

        request.add_package(package_object, **package_attrs)

    for dep_and_replaces in payload.get("dependencies", []):
        dep = copy.deepcopy(dep_and_replaces)
        replaces = dep.pop("replaces", None)

        dep_object = Dependency.get_or_create(dep)
        replaces_object = None
        if replaces:
            replaces_object = Dependency.get_or_create(replaces)
        request.add_dependency(package_object, dep_object, replaces_object)

    for env_var_name, env_var_info in payload.get("environment_variables", {}).items():
        env_var_obj = EnvironmentVariable.query.filter_by(name=env_var_name, **env_var_info).first()
        if not env_var_obj:
            env_var_obj = EnvironmentVariable.from_json(env_var_name, env_var_info)
            db.session.add(env_var_obj)

        if env_var_obj not in request.environment_variables:
            request.environment_variables.append(env_var_obj)

    db.session.commit()

    bundle_dir = RequestBundleDir(request.id, root=flask.current_app.config["CACHITO_BUNDLES_DIR"])

    if delete_bundle and bundle_dir.bundle_archive_file.exists():
        flask.current_app.logger.info(
            "Deleting the bundle archive %s", bundle_dir.bundle_archive_file
        )
        try:
            bundle_dir.bundle_archive_file.unlink()
        except:  # noqa E722
            flask.current_app.logger.exception(
                "Failed to delete the bundle archive %s", bundle_dir.bundle_archive_file
            )

    if delete_bundle_temp and bundle_dir.exists():
        flask.current_app.logger.debug(
            "Deleting the temporary files used to create the bundle at %s", bundle_dir
        )
        try:
            bundle_dir.rmtree()
        except:  # noqa E722
            flask.current_app.logger.exception(
                "Failed to delete the temporary files at %s", bundle_dir
            )

    if delete_logs:
        request_log_dir = flask.current_app.config["CACHITO_REQUEST_FILE_LOGS_DIR"]
        path_to_file = os.path.join(request_log_dir, f"{request_id}.log")
        try:
            os.remove(path_to_file)
        except:  # noqa E722
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

    return flask.jsonify(request.to_json()), 200


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
