# SPDX-License-Identifier: GPL-3.0-or-later
from celery import Celery
from celery.signals import celeryd_init

from cachito.workers.config import configure_celery, validate_celery_config


app = Celery(include=['cachito.workers.tasks.general', 'cachito.workers.tasks.golang'])
configure_celery(app)
celeryd_init.connect(validate_celery_config)
