# SPDX-License-Identifier: GPL-3.0-or-later
from flask import render_template, Blueprint


splash = Blueprint('splash', __name__)


@splash.route('/', methods=['GET'])
def index():
    return render_template('index.html')
