# SPDX-License-Identifier: GPL-3.0-or-later
from unittest.mock import patch

from sqlalchemy.exc import NoSuchTableError


def test_health_check(client, db):
    rv = client.get('/healthcheck')
    assert rv.status_code == 200
    assert rv.data == b'OK'


def test_health_check_failed(client, db):
    with patch('cachito.web.app.db.session.execute') as mock_execute:
        mock_execute.side_effect = NoSuchTableError()
        rv = client.get('/healthcheck')
    assert rv.status_code == 500
    assert rv.json.keys() == {'error'}
