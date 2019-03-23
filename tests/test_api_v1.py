# SPDX-License-Identifier: GPL-3.0-or-later
import json


def test_ping(client):
    rv = client.get('/api/v1/ping')
    assert json.loads(rv.data.decode('utf-8')) is True
