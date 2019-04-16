# SPDX-License-Identifier: GPL-3.0-or-later
import re

import flask

from cachito.errors import ValidationError
from cachito.web import db
from cachito.web.models import Request
from cachito.workers import tasks


api_v1 = flask.Blueprint('api_v1', __name__)


@api_v1.route('/ping', methods=['GET'])
def ping():
    return flask.jsonify(True)


@api_v1.route('/ping-celery', methods=['GET'])
def ping_celery():
    tasks.add.delay(4, 4)
    return flask.jsonify(True)


@api_v1.route('/requests/<request_id>', methods=['GET'])
def get_request(request_id):
    """
    Retrieve details for the given request.

    :param int request_id: the value of the request ID
    :return: a Flask JSON response
    :rtype: flask.Response
    :raise NotFound: if the request is not found
    :raise ValidationError: if the request ID is of invalid format
    """
    try:
        request_id = int(request_id)
    except ValueError:
        raise ValidationError(f'{request_id} is not a valid request ID')
    request = Request.query.get_or_404(request_id)
    return flask.jsonify(request.to_json())


@api_v1.route('/requests', methods=['POST'])
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

    # TODO: Setup authentication

    request = Request.from_json(payload)
    db.session.add(request)
    db.session.commit()

    if not re.match(r'^[a-f0-9]{40}', request.ref):
        raise ValidationError('The "ref" parameter must be a 40 character hex string')

    tasks.fetch_app_source.delay(request.repo, request.ref)
    return flask.jsonify(request.to_json()), 201


@api_v1.errorhandler(ValidationError)
def handle_validation_error(e):
    return flask.jsonify(error=str(e)), 400
