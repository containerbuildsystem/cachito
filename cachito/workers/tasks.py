# SPDX-License-Identifier: GPL-3.0-or-later
import logging

from celery import Celery

from cachito.workers.config import configure_celery

log = logging.getLogger(__name__)
logging.basicConfig()
app = Celery()
configure_celery(app)


@app.task
def add(x, y):
    """Add two numbers together to prove Celery works"""
    return x + y
