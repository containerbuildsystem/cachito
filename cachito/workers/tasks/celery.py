# SPDX-License-Identifier: GPL-3.0-or-later
import sys

import celery
from celery.signals import celeryd_init, task_postrun, task_prerun

from cachito.workers.celery_logging import cleanup_task_logging, setup_task_logging
from cachito.workers.config import configure_celery, validate_celery_config


# Workaround https://github.com/celery/celery/issues/5416
if celery.version_info < (4, 3) and sys.version_info >= (3, 7):  # pragma: no cover
    from re import Pattern
    from celery.app.routes import re as routes_re

    routes_re._pattern_type = Pattern


app = celery.Celery()
configure_celery(app)
celeryd_init.connect(validate_celery_config)
task_prerun.connect(setup_task_logging)
task_postrun.connect(cleanup_task_logging)
