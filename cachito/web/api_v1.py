# SPDX-License-Identifier: GPL-3.0-or-later
from flask import Blueprint, jsonify

from cachito.workers import tasks


api_v1 = Blueprint('api_v1', __name__)


@api_v1.route('/ping', methods=['GET'])
def ping():
    return jsonify(True)


@api_v1.route('/ping-celery', methods=['GET'])
def ping_celery():
    tasks.add.delay(4, 4)
    return jsonify(True)
