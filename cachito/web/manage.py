# SPDX-License-Identifier: GPL-3.0-or-later
import time

import click
from flask.cli import FlaskGroup
from sqlalchemy.exc import OperationalError

from cachito.web.models import db
from cachito.web.app import create_cli_app


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


@cli.command(name="test-performance")
def test_performance():
    """Run performance tests."""
    import copy
    import os.path
    from collections import defaultdict
    from contextlib import contextmanager
    from functools import cache
    import json
    import requests
    from cachito.web.models import Dependency, Package, Request

    @contextmanager
    def measure_duration(durations, name):
        start = time.time()
        try:
            yield
        finally:
            duration = time.time() - start
            durations[name] += duration

    @cache
    def download_packages_json(url):
        response = requests.get(url)
        response.raise_for_status()
        return response.json()['packages']

    def single_update(request, package, deps_json, durations):
        for dep_and_replaces in deps_json:
            dep_json = copy.deepcopy(dep_and_replaces)
            replaces_json = dep_json.pop("replaces", None)

            with measure_duration(durations, 'get_or_create_dep'):
                dep = Dependency.get_or_create(dep_json)

            replaces = None
            if replaces_json:
                with measure_duration(durations, 'get_or_create_dep'):
                    replaces = Dependency.get_or_create(replaces_json)

            with measure_duration(durations, 'add_dep'):
                request.add_dependency(package, dep, replaces)

    def bulk_update(request, package, deps_json, durations):
        bulk_dependencies = []
        mapped_bulk_dependencies = {}
        relationships = []
        for dep_and_replaces in deps_json:
            dep_json = copy.deepcopy(dep_and_replaces)
            replaces_json = dep_json.pop("replaces", None)
            bulk_dependencies.append(dep_json)
            if replaces_json:
                bulk_dependencies.append(replaces_json)

        bulk_package_dependencies = []
        if bulk_dependencies:
            with measure_duration(durations, 'get_or_create_dep'):
                created_bulk_dependencies = Dependency.bulk_get_or_create(bulk_dependencies)
            mapped_bulk_dependencies = {
                (dep.name, dep.version, dep.type, dep.dev): dep for dep in created_bulk_dependencies
            }
            for dep_and_replaces in deps_json:
                dep_key = (
                    dep_and_replaces['name'],
                    dep_and_replaces['version'],
                    dep_and_replaces['type'],
                    dep_and_replaces.get('dev', False),
                )
                dep = mapped_bulk_dependencies[dep_key]

                replaces = None
                replaces_json = dep_and_replaces.get("replaces", None)
                if replaces_json:
                    replaces_key = (
                        replaces_json['name'],
                        replaces_json['version'],
                        replaces_json['type'],
                        replaces_json.get('dev', False),
                    )
                    replaces = mapped_bulk_dependencies[replaces_key]
                bulk_package_dependencies.append(
                    (
                        dep,
                        replaces,
                    )
                )

        if bulk_package_dependencies:
            with measure_duration(durations, 'add_dep'):
                request.bulk_add_dependency(package, bulk_package_dependencies)

    large_url = (
        'https://gist.githubusercontent.com/lcarva/8af91ba1cd630386997728cb663dc69b/raw/'
        'd32c71446346125ab2c58a63fd215aec550da861/large-cachito-request.json'
    )

    small_url = (
        'https://gist.githubusercontent.com/lcarva/7df2d7b152e2622a5290412e8cc7fb4b/raw/'
        'ffe43d16dd00f9a678b0abe73b2c55059db3c7a6/small-cachito-request.json'
    )

    scenarios = (
        (small_url, bulk_update, 30),
        (small_url, bulk_update, 50),
        (small_url, bulk_update, 300),
        (small_url, bulk_update, 500),
        (small_url, bulk_update, 1000),
        (small_url, single_update, 30),
        (small_url, single_update, 50),
        (small_url, single_update, 300),
        (small_url, single_update, 500),
        (small_url, single_update, 1000),
        (large_url, bulk_update, 30),
        (large_url, bulk_update, 50),
        (large_url, bulk_update, 300),
        (large_url, bulk_update, 500),
        (large_url, bulk_update, 1000),
        (large_url, single_update, 30),
        (large_url, single_update, 50),
        (large_url, single_update, 300),
        (large_url, single_update, 500),
        (large_url, single_update, 1000),
    )

    results = []

    for url, update, batch_size in scenarios:
        packages = copy.deepcopy(download_packages_json(url))
        size = os.path.basename(url).split('.')[0].replace('-cachito-request', '')
        scenario_name = f'{size}_{update.__name__}_{batch_size}'
        durations = defaultdict(int)
        request = Request.from_json(
            {
                'repo': scenario_name,
                'ref': '2ba62c8cfc98b74a6d02e692ce181add8b2184cd',
                'pkg_managers': ['gomod'],
            }
        )
        db.session.add(request)
        db.session.commit()

        print(f'Running scenario {scenario_name} on request {request.id}')

        with measure_duration(durations, 'deps_overall'):
            for package_json in copy.deepcopy(packages):
                all_deps = package_json.pop('dependencies', [])
                package = Package.get_or_create(package_json)
                for index in range(0, len(all_deps), batch_size):
                    batch_upper_limit = index + batch_size
                    deps_chunk = all_deps[index:batch_upper_limit]
                    update(request, package, deps_chunk, durations)
                    with measure_duration(durations, 'deps_commit'):
                        db.session.commit()

        request.add_state('complete', json.dumps(durations))
        db.session.commit()
        results.append((scenario_name, durations))

    if results:
        print()
        headers = ['Scenario'.ljust(24)] + [key.ljust(17) for key in results[0][1].keys()]
        headers_line = ' | '.join(headers)
        print(headers_line)
        print('_' * len(headers_line))

        for result in results:
            # import pdb

            # pdb.set_trace()
            row = [result[0].ljust(24)] + [
                f'{result[1][key.strip()]:.4f}'.ljust(17) for key in headers[1:]
            ]
            print(' | '.join(row))


if __name__ == "__main__":
    cli()
