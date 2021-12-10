# SPDX-License-Identifier: GPL-3.0-or-later
import sys

import celery
from celery.signals import celeryd_init, task_postrun, task_prerun

from cachito.workers.celery_logging import (
    cleanup_task_logging,
    cleanup_task_logging_customization,
    setup_task_logging,
    setup_task_logging_customization,
)
from cachito.workers.config import app  # noqa: F401
from cachito.workers.config import populate_ssh_known_hosts, validate_celery_config

# Workaround https://github.com/celery/celery/issues/5416
if celery.version_info < (4, 3) and sys.version_info >= (3, 7):  # pragma: no cover
    from re import Pattern

    from celery.app.routes import re as routes_re

    routes_re._pattern_type = Pattern


celeryd_init.connect(validate_celery_config)
celeryd_init.connect(populate_ssh_known_hosts)
task_prerun.connect(setup_task_logging_customization)
task_prerun.connect(setup_task_logging)
task_postrun.connect(cleanup_task_logging_customization)
task_postrun.connect(cleanup_task_logging)
