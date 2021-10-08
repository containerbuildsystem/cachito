# SPDX-License-Identifier: GPL-3.0-or-later
from flask import jsonify
from werkzeug.exceptions import HTTPException

from cachito.errors import (
    CachitoError,
    CachitoNotImplementedError,
    ContentManifestError,
    ValidationError,
)


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
        elif isinstance(error, ContentManifestError):
            # If the request was completed and a ICM cannot be generated,
            # some package type or corner case is not yet implemented
            status_code = 501
        elif isinstance(error, CachitoNotImplementedError):
            # If the request asks for not implemented functionality
            status_code = 501
        elif isinstance(error, CachitoError):
            # If a generic exception is raised, assume the service is unavailable
            status_code = 503

        response = jsonify({"error": msg})
        response.status_code = status_code
    return response


def validation_error(error):
    """
    Handle pydandic ValidationError.

    Prepare JSON response in the following format:
    {
      "errors": {
        "field1": "error message",
        ...
      }
    }

    :param Exception error: validation error
    :return: a Flask JSON response
    :rtype: flask.Response
    """
    errors = {".".join(error["loc"]): error["msg"] for error in error.errors()}
    response = jsonify({"errors": errors})
    response.status_code = 400
    return response
