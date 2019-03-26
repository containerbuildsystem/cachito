# SPDX-License-Identifier: GPL-3.0-or-later

from cachito.web import db


class Request(db.Model):
    """A Cachito user request."""
    id = db.Column(db.Integer, primary_key=True)

    def __repr__(self):
        return '<Request {0!r}>'.format(self.id)
