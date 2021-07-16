# SPDX-License-Identifier: GPL-3.0-or-later
import time

import click
from flask.cli import FlaskGroup
from sqlalchemy.exc import OperationalError

from cachito.web.app import create_cli_app
from cachito.web.models import db


@click.group(cls=FlaskGroup, create_app=create_cli_app)
def cli():
    """Manage the Cachito Flask application."""


@cli.command(name="wait-for-db")
def wait_for_db():
    """Wait until database server is reachable."""
    # The polling interval in seconds
    poll_interval = 10
    while True:
        try:
            db.engine.connect()
        except OperationalError as e:
            click.echo("Failed to connect to database: {}".format(e), err=True)
            click.echo("Sleeping for {} seconds...".format(poll_interval))
            time.sleep(poll_interval)
            click.echo("Retrying...")
        else:
            break


if __name__ == "__main__":
    cli()
