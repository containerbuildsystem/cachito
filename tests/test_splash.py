# # SPDX-License-Identifier: GPL-3.0-or-later


def test_index(client):
    rv = client.get('/')
    assert 'Welcome to cachito' in rv.data.decode('utf-8')
