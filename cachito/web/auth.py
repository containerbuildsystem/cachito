# SPDX-License-Identifier: GPL-3.0-or-later
from flask import current_app

from cachito.web import db
from cachito.web.models import User


def user_loader(username):
    """
    Get the user by their username from the database.

    This is used by the Flask-Login library.

    :param str username: the username of the user
    :return: the User object associated with the username or None
    :rtype: cachito.web.models.User
    """
    return User.query.filter_by(username=username).first()


def load_user_from_request(request):
    """
    Load the user that authenticated from the current request.

    When authentication is turned on (default in production), this relies on the "REMOTE_USER"
    environment variable being set. This is usually set by the mod_auth_gssapi Apache authentication
    module.

    This is used by the Flask-Login library. If the user does not exist in the database, an entry
    will be created.

    If None is returned, then Flask-Login will set `flask_login.current_user` to an
    `AnonymousUserMixin` object, which has the `is_authenticated` property set to `False`.
    Additionally, any route decorated with `@login_required` will raise an `Unauthorized` exception.

    :param flask.Request request: the Flask request
    :return: the User object associated with the username or None
    :rtype: cachito.web.models.User
    """
    remote_user = request.environ.get('REMOTE_USER')
    if not remote_user:
        if current_app.config.get('LOGIN_DISABLED', False) is True:
            current_app.logger.info(
                'The REMOTE_USER environment variable wasn\'t set on the request, but the '
                'LOGIN_DISABLED configuration is set to True.'
            )
        return

    username = remote_user
    current_app.logger.info(f'The user "{username}" was authenticated successfully by httpd')

    user = User.get_or_create(username)
    if not user.id:
        db.session.commit()

    return user
