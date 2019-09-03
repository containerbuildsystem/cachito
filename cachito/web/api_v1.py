# SPDX-License-Identifier: GPL-3.0-or-later
import os
import re

from celery import chain
import flask
from flask_login import current_user, login_required
from werkzeug.exceptions import Unauthorized, InternalServerError

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
        value exceeds configuration's CACHITO_MAX_PER_PAGE.
    :rtype: flask.Response
    """
    max_per_page = flask.current_app.config['CACHITO_MAX_PER_PAGE']
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
    if request.last_state.state_name != 'complete':
        raise ValidationError(
            'The request must be in the "complete" state before downloading the archive')

    if not os.path.exists(request.bundle_archive):
        flask.current_app.logger.error(
            'The bundle archive at %s for request %d doesn\'t exist',
            request.bundle_archive, request_id,
        )
        raise InternalServerError()

    flask.current_app.logger.debug(
        'Sending the bundle at %s for request %d',
        request.bundle_archive, request_id,
    )
    return flask.send_file(request.bundle_archive, mimetype='application/gzip')


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
    if not isinstance(payload, dict):
        raise ValidationError('The input data must be a JSON object')

    request = Request.from_json(payload)
    if not re.match(r'^[a-f0-9]{40}', request.ref):
        raise ValidationError('The "ref" parameter must be a 40 character hex string')
    db.session.add(request)
    db.session.commit()

    if current_user.is_authenticated:
        flask.current_app.logger.info(
            'The user %s submitted request %d', current_user.username, request.id)
    else:
        flask.current_app.logger.info('An anonymous user submitted request %d', request.id)

    db.session.add(request)
    db.session.commit()

    # Chain tasks
    error_callback = tasks.failed_request_callback.s(request.id)
    chain(
        tasks.fetch_app_source.s(
            request.repo, request.ref, request_id_to_update=request.id).on_error(error_callback),
        tasks.fetch_gomod_source.s(request_id_to_update=request.id).on_error(error_callback),
        tasks.set_request_state.si(request.id, 'complete', 'Completed successfully'),
    ).delay()

    flask.current_app.logger.debug('Successfully scheduled request %d', request.id)
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
            raise ValidationError('The value for "{}" must be a string'.format(key))

    if 'state' in payload and 'state_reason' not in payload:
        raise ValidationError('The "state_reason" key is required when "state" is supplied')
    elif 'state_reason' in payload and 'state' not in payload:
        raise ValidationError('The "state" key is required when "state_reason" is supplied')

    request = Request.query.get_or_404(request_id)
    delete_bundle = False
    if 'state' in payload and 'state_reason' in payload:
        last_state = request.last_state
        new_state = payload['state']
        delete_bundle = new_state in ('failed', 'stale')
        new_state_reason = payload['state_reason']
        # This is to protect against a Celery task getting executed twice and setting the
        # state each time
        if last_state.state_name == new_state and last_state.state_reason == new_state_reason:
            flask.current_app.logger.info('Not adding a new state since it matches the last state')
        else:
            request.add_state(new_state, new_state_reason)

    if 'dependencies' in payload:
        for dep in payload['dependencies']:
            dep_obj = Dependency.query.filter_by(**dep).first()
            if not dep_obj:
                dep_obj = Dependency.from_json(dep)
                db.session.add(dep_obj)

            if dep_obj not in request.dependencies:
                request.dependencies.append(dep_obj)

    db.session.commit()
    if delete_bundle and os.path.exists(request.bundle_archive):
        flask.current_app.logger.info('Deleting the bundle archive %s', request.bundle_archive)
        try:
            os.remove(request.bundle_archive)
        except:  # noqa E722
            flask.current_app.logger.exception(
                'Failed to delete the bundle archive %s', request.bundle_archive)

    if current_user.is_authenticated:
        flask.current_app.logger.info(
            'The user %s patched request %d', current_user.username, request.id)
    else:
        flask.current_app.logger.info('An anonymous user patched request %d', request.id)

    return flask.jsonify(request.to_json()), 200
