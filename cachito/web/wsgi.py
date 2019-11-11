# SPDX-License-Identifier: GPL-3.0-or-later
from cachito.web.app import create_app
from cachito.web.config import validate_cachito_config

app = create_app()
validate_cachito_config(app.config)
