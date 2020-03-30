# SPDX-License-Identifier: GPL-3.0-or-later
import copy

from celery import chain
import flask
from flask_login import current_user, login_required
from werkzeug.exceptions import Unauthorized, InternalServerError

from cachito.errors import ValidationError
from cachito.web import db
from cachito.web.models import (
    Dependency,
    EnvironmentVariable,
    Package,
    PackageManager,
    Request,
    RequestState,
    RequestStateMapping,
)
from cachito.web.utils import pagination_metadata, str_to_bool
from cachito.workers import tasks
from cachito.paths import RequestBundleDir

api_v1 = flask.Blueprint("api_v1", __name__)


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
    query = Request.query
    if state:
        if state not in RequestStateMapping.get_state_names():
            states = ", ".join(RequestStateMapping.get_state_names())
            raise ValidationError(
                f"{state} is not a valid request state. Valid states are: {states}"
            )
        state_int = RequestStateMapping.__members__[state].value
        query = query.join(RequestState, Request.request_state_id == RequestState.id)
        query = query.filter(RequestState.state == state_int)
    pagination_query = query.paginate(max_per_page=max_per_page)
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
    return flask.send_file(str(bundle_dir.bundle_archive_file), mimetype="application/gzip")


@api_v1.route("/requests", methods=["POST"])
@login_required
def create_request():
    """
    Submit a request to resolve and cache the given source code and its dependencies.

    :param str repo: the URL to the SCM repository
    :param str ref: the SCM reference to fetch
    :param list<str> pkg_managers: list of package managers to be used for resolving dependencies
    :param list<str> flags: list of flag names
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
    auto_detect = len(pkg_manager_names) == 0
    if auto_detect:
        flask.current_app.logger.info(
            'Automatic detection will be used since "pkg_managers" was empty'
        )

    # Chain tasks
    error_callback = tasks.failed_request_callback.s(request.id)
    chain_tasks = [
        tasks.fetch_app_source.s(request.repo, request.ref, request.id).on_error(error_callback)
    ]
    if "gomod" in pkg_manager_names or auto_detect:
        gomod_dependency_replacements = [
            dependency_replacement
            for dependency_replacement in payload.get("dependency_replacements", [])
            if dependency_replacement["type"] == "gomod"
        ]
        chain_tasks.append(
            tasks.fetch_gomod_source.si(
                request.id, auto_detect, gomod_dependency_replacements
            ).on_error(error_callback)
        )

    chain_tasks.extend(
        [
            tasks.create_bundle_archive.si(request.id).on_error(error_callback),
            tasks.set_request_state.si(request.id, "complete", "Completed successfully"),
        ]
    )

    chain(chain_tasks).delay()
    flask.current_app.logger.debug("Successfully scheduled request %d", request.id)
    return flask.jsonify(request.to_json()), 201


@api_v1.route("/requests/<int:request_id>", methods=["PATCH"])
@login_required
def patch_request(request_id):
    """
    Modify the given request.

    :param int request_id: the request ID from the URL
    :return: a Flask JSON response
    :rtype: flask.Response
    :raise NotFound: if the request is not found
    :raise ValidationError: if the JSON is invalid
    """
    allowed_users = flask.current_app.config["CACHITO_WORKER_USERNAMES"]
    # current_user.is_authenticated is only ever False when auth is disabled
    if current_user.is_authenticated and current_user.username not in allowed_users:
        raise Unauthorized("This API endpoint is restricted to Cachito workers")

    payload = flask.request.get_json()
    if not isinstance(payload, dict):
        raise ValidationError("The input data must be a JSON object")

    if not payload:
        raise ValidationError("At least one key must be specified to update the request")

    valid_keys = {
        "dependencies",
        "environment_variables",
        "packages",
        "pkg_managers",
        "state",
        "state_reason",
    }
    invalid_keys = set(payload.keys()) - valid_keys
    if invalid_keys:
        raise ValidationError(
            "The following keys are not allowed: {}".format(", ".join(invalid_keys))
        )

    for key, value in payload.items():
        if key in ("dependencies", "packages", "pkg_managers") and not isinstance(value, list):
            raise ValidationError(f'The value for "{key}" must be an array')

        if key == "dependencies":
            for dep in value:
                Dependency.validate_json(dep, for_update=True)
        elif key == "packages":
            for dep in value:
                Package.validate_json(dep)
        elif key == "pkg_managers":
            for pkg_manager in value:
                if not isinstance(pkg_manager, str):
                    raise ValidationError(
                        'The value for "pkg_managers" must be an array of strings'
                    )
        elif key == "environment_variables":
            if not isinstance(value, dict):
                raise ValidationError('The value for "{}" must be an object'.format(key))
            for env_var_name, env_var_value in value.items():
                EnvironmentVariable.validate_json(env_var_name, env_var_value)
        elif not isinstance(value, str):
            raise ValidationError('The value for "{}" must be a string'.format(key))

    if "state" in payload and "state_reason" not in payload:
        raise ValidationError('The "state_reason" key is required when "state" is supplied')
    elif "state_reason" in payload and "state" not in payload:
        raise ValidationError('The "state" key is required when "state_reason" is supplied')

    request = Request.query.get_or_404(request_id)
    delete_bundle = False
    delete_bundle_temp = False
    if "state" in payload and "state_reason" in payload:
        new_state = payload["state"]
        delete_bundle = new_state == "stale"
        delete_bundle_temp = new_state in ("complete", "failed")
        new_state_reason = payload["state_reason"]
        # This is to protect against a Celery task getting executed twice and setting the
        # state each time
        if request.state.state_name == new_state and request.state.state_reason == new_state_reason:
            flask.current_app.logger.info("Not adding a new state since it matches the last state")
        else:
            request.add_state(new_state, new_state_reason)

    if "dependencies" in payload:
        for dep_and_replaces in payload["dependencies"]:
            dep = copy.deepcopy(dep_and_replaces)
            replaces = dep.pop("replaces", None)

            dep_object = Dependency.get_or_create(dep)
            replaces_object = None
            if replaces:
                replaces_object = Dependency.get_or_create(replaces)
            request.add_dependency(dep_object, replaces_object)

    for package in payload.get("packages", []):
        package_object = Package.get_or_create(package)
        if package_object not in request.packages:
            request.packages.append(package_object)

    if "pkg_managers" in payload:
        pkg_managers = PackageManager.get_pkg_managers(payload["pkg_managers"])
        for pkg_manager in pkg_managers:
            if pkg_manager not in request.pkg_managers:
                request.pkg_managers.append(pkg_manager)

    for name, value in payload.get("environment_variables", {}).items():
        env_var_obj = EnvironmentVariable.query.filter_by(name=name, value=value).first()
        if not env_var_obj:
            env_var_obj = EnvironmentVariable.from_json(name, value)
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

    if current_user.is_authenticated:
        flask.current_app.logger.info(
            "The user %s patched request %d", current_user.username, request.id
        )
    else:
        flask.current_app.logger.info("An anonymous user patched request %d", request.id)

    return flask.jsonify(request.to_json()), 200
