# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import os
import re

from celery import chain
import flask
from flask_login import current_user, login_required
from werkzeug.exceptions import Unauthorized

from cachito.errors import ValidationError
from cachito.web import db
from cachito.web.models import Request, Dependency
from cachito.workers import tasks


api_v1 = flask.Blueprint('api_v1', __name__)


@api_v1.route('/requests', methods=['GET'])
def get_requests():
    """
    Retrieve paginated details for requests.

    :param int page: the page number to retrieve. Defaults to 1
    :param int per_page: the amount of items on each page. Defaults to 20. Ignored if
        value exceeds configuration's MAX_PER_PAGE.
    :rtype: flask.Response
    """
    max_per_page = flask.current_app.config['MAX_PER_PAGE']
    # The call to `paginate` will inspect the current HTTP request for the
    # pagination parameters `page` and `per_page`.
    requests = Request.query.paginate(max_per_page=max_per_page).items
    response = {
        'items': [request.to_json() for request in requests],
    }
    return flask.jsonify(response)


@api_v1.route('/requests/<int:request_id>', methods=['GET'])
def get_request(request_id):
    """
    Retrieve details for the given request.

    :param int request_id: the value of the request ID
    :return: a Flask JSON response
    :rtype: flask.Response
    :raise NotFound: if the request is not found
    """
    return flask.jsonify(Request.query.get_or_404(request_id).to_json())


@api_v1.route('/requests/<int:request_id>/download', methods=['GET'])
def download_archive(request_id):
    """
    Download archive of source code.

    :param int request_id: the value of the request ID
    :return: a Flask send_file response
    :rtype: flask.Response
    :raise NotFound: if the request is not found
    """
    request = Request.query.get_or_404(request_id)
    # TODO: Verify request has already been processed.

    cachito_shared_dir = flask.current_app.config['CACHITO_SHARED_DIR']
    wait_timeout = flask.current_app.config['CACHITO_WAIT_TIMEOUT']

    with tempfile.TemporaryDirectory(prefix='cachito-', dir=cachito_shared_dir) as temp_dir:
        # Although the cachito_shared_dir volume is required to be the same between celery
        # workers and the API, they may be mounted at different locations. Use relative
        # paths to agree on data location within the shared volume.
        relative_temp_dir = os.path.basename(temp_dir)
        relative_deps_path = os.path.join(relative_temp_dir, 'deps')
        relative_bundle_archive_path = os.path.join(relative_temp_dir, 'bundle.tar.gz')
        absolute_bundle_archive_path = os.path.join(
            cachito_shared_dir, relative_bundle_archive_path)

        # Chain tasks
        chain_result = chain(
            tasks.fetch_app_source.s(request.repo, request.ref),
            tasks.fetch_gomod_source.s(copy_cache_to=relative_deps_path),
            tasks.assemble_source_code_archive.s(
                deps_path=relative_deps_path, bundle_archive_path=relative_bundle_archive_path)
        ).delay()
        chain_result.wait(timeout=wait_timeout)

        return flask.send_file(absolute_bundle_archive_path, mimetype='application/gzip')


@api_v1.route('/requests', methods=['POST'])
@login_required
def create_request():
    """
    Submit a request to resolve and cache the given source code and its dependencies.

    :param str repo: the URL to the SCM repository
    :param str ref: the SCM reference to fetch
    :param list<str> pkg_managers: list of package managers to be used for resolving dependencies
    :rtype: flask.Response
    :raise ValidationError: if required parameters are not supplied
    """
    payload = flask.request.get_json()

    request = Request.from_json(payload)
    db.session.add(request)
    db.session.commit()

    if not re.match(r'^[a-f0-9]{40}', request.ref):
        raise ValidationError('The "ref" parameter must be a 40 character hex string')

    # Chain tasks
    error_callback = tasks.failed_request_callback.s(request.id)
    chain(
        tasks.fetch_app_source.s(
            request.repo, request.ref, request_id_to_update=request.id).on_error(error_callback),
        tasks.fetch_gomod_source.s(request_id_to_update=request.id).on_error(error_callback),
        tasks.set_request_state.si(request.id, 'complete', 'Completed successfully'),
    ).delay()

    return flask.jsonify(request.to_json()), 201


@api_v1.route('/requests/<int:request_id>', methods=['PATCH'])
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
    # Convert the allowed users to lower-case since they are stored in the database as lower-case
    # for consistency
    allowed_users = [user.lower() for user in flask.current_app.config['CACHITO_WORKER_USERNAMES']]
    # current_user.is_authenticated is only ever False when auth is disabled
    if current_user.is_authenticated and current_user.username not in allowed_users:
        raise Unauthorized('This API endpoint is restricted to Cachito workers')

    payload = flask.request.get_json()
    if not isinstance(payload, dict):
        raise ValidationError('The input data must be a JSON object')

    if not payload:
        raise ValidationError('At least one key must be specified to update the request')

    valid_keys = {'dependencies', 'state', 'state_reason'}
    invalid_keys = set(payload.keys()) - valid_keys
    if invalid_keys:
        raise ValidationError(
            'The following keys are not allowed: {}'.format(', '.join(invalid_keys)))

    for key, value in payload.items():
        if key == 'dependencies':
            if not isinstance(value, list):
                raise ValidationError('The value for "dependencies" must be an array')
            for dep in value:
                Dependency.validate_json(dep)
        elif not isinstance(value, str):
            raise ValidationError(
                'The value for "{}" must be a string. It was the type {}.'
                .format(key, type(value).__name__)
            )

    if 'state' in payload and 'state_reason' not in payload:
        raise ValidationError('The "state_reason" key is required when "state" is supplied')
    elif 'state_reason' in payload and 'state' not in payload:
        raise ValidationError('The "state" key is required when "state_reason" is supplied')

    request = Request.query.get_or_404(request_id)
    if 'state' in payload and 'state_reason' in payload:
        request.add_state(payload['state'], payload['state_reason'])

    if 'dependencies' in payload:
        for dep in payload['dependencies']:
            dep_obj = Dependency.query.filter_by(**dep).first()
            if not dep_obj:
                dep_obj = Dependency.from_json(dep)
                db.session.add(dep_obj)

            if dep_obj not in request.dependencies:
                request.dependencies.append(dep_obj)

    db.session.commit()

    return flask.jsonify(request.to_json()), 200
