# SPDX-License-Identifier: GPL-3.0-or-later
from flask import jsonify
from werkzeug.exceptions import HTTPException

from cachito.errors import CachitoError, ValidationError


def json_error(error):
    """
    Convert exceptions to JSON responses.

    :param Exception error: an Exception to convert to JSON
    :return: a Flask JSON response
    :rtype: flask.Response
    """
    if isinstance(error, HTTPException):
        if error.code == 404:
            msg = "The requested resource was not found"
        else:
            msg = error.description
        response = jsonify({"error": msg})
        response.status_code = error.code
    else:
        status_code = 500
        msg = str(error)
        if isinstance(error, ValidationError):
            status_code = 400
        elif isinstance(error, CachitoError):
            # If a generic exception is raised, assume the service is unavailable
            status_code = 503

        response = jsonify({"error": msg})
        response.status_code = status_code
    return response
