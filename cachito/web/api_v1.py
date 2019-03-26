# SPDX-License-Identifier: GPL-3.0-or-later
from flask import Blueprint, jsonify


api_v1 = Blueprint('api_v1', __name__)


@api_v1.route('/ping', methods=['GET'])
def ping():
    return jsonify(True)
