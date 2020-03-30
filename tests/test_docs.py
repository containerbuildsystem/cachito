# # SPDX-License-Identifier: GPL-3.0-or-later


def test_docs(client):
    rv = client.get("/")
    assert "Cachito API Documentation" in rv.data.decode("utf-8")
