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


def _get_kerberos_principal(request):
    """
    Get the Kerberos principal from the current request.

    This relies on the "REMOTE_USER" environment variable being set. This is usually set by the
    mod_auth_gssapi Apache authentication module.

    :param flask.Request request: the Flask request
    :return: the user's Kerberos principal or None
    :rtype: str
    """
    return request.environ.get("REMOTE_USER")


def _get_cert_dn(request):
    """
    Get the client certificate's subject's distinguished name.

    This relies on the "SSL_CLIENT_S_DN" environment variable being set. This is set by the mod_ssl
    Apache module. If Apache is unable to verify the client certificate, no user will be returned.

    :param flask.Request request: the Flask request
    :return: the client certificate's subject's distinguished name or None
    :rtype: str
    """
    ssl_client_verify = request.environ.get("SSL_CLIENT_VERIFY")
    if ssl_client_verify != "SUCCESS":
        current_app.logger.debug(
            "The SSL_CLIENT_VERIFY environment variable was set to %s", ssl_client_verify
        )
        return

    return request.environ.get("SSL_CLIENT_S_DN")


def load_user_from_request(request):
    """
    Load the user that authenticated from the current request.

    This is used by the Flask-Login library. If the user does not exist in the database, an entry
    will be created.

    If None is returned, then Flask-Login will set `flask_login.current_user` to an
    `AnonymousUserMixin` object, which has the `is_authenticated` property set to `False`.
    Additionally, any route decorated with `@login_required` will raise an `Unauthorized` exception.

    :param flask.Request request: the Flask request
    :return: the User object associated with the username or None
    :rtype: cachito.web.models.User
    """
    username = _get_kerberos_principal(request) or _get_cert_dn(request)
    if not username:
        if current_app.config.get("LOGIN_DISABLED", False) is True:
            current_app.logger.info(
                "The REMOTE_USER environment variable wasn't set on the request, but the "
                "LOGIN_DISABLED configuration is set to True."
            )
        return

    current_app.logger.info(f'The user "{username}" was authenticated successfully by httpd')
    user = User.get_or_create(username)
    if not user.id:
        db.session.commit()

    return user
