# cachito

Caching service for source code

## Quick Start

Run the application locally (requires [docker-compose](https://docs.docker.com/compose/)):

    make run

Verify in the browser at [http://localhost:8080/](http://localhost:8080/)

Use curl to make requests:

    # List all requests
    curl http://localhost:8080/api/v1/requests

    # Create a new request
    curl -X POST -H "Content-Type: application/json" http://localhost:8080/api/v1/requests -d \
        '{
           "repo": "https://github.com/release-engineering/retrodep.git",
           "ref": "e1be527f39ec31323f0454f7d1422c6260b00580",
           "pkg_managers": ["gomod"]
         }'

    # Check the status of a request
    curl http://localhost:8080/api/v1/requests/1

    # Download the source archive for a completed request
    curl http://localhost:8080/api/v1/requests/1/download -o source.tar.gz


## Pre-built Container Images

Cachito container images are automatically built when changes are merged. There are two images,
an httpd based image with the Cachito API, and a Celery worker image with the Cachito worker code.

[![cachito-api](https://quay.io/repository/factory2/cachito-api/status)](https://quay.io/repository/factory2/cachito-api)
  `quay.io/factory2/cachito-api:latest`

[![cachito-worker](https://quay.io/repository/factory2/cachito-worker/status)](https://quay.io/repository/factory2/cachito-worker)
  `quay.io/factory2/cachito-worker:latest`

## Prerequisites

This is built to be used with Python 3.

Some Flask dependencies are compiled during installation, so `gcc` and Python header files need to be present.
For example, on Fedora:

    dnf install gcc python3-devel

## Development environment and release process

- create virtualenv with Flask and cachito installed into it (latter is installed in
  [develop mode](http://setuptools.readthedocs.io/en/latest/setuptools.html#development-mode) which allows
  modifying source code directly without a need to re-install the app): `make venv`

- run development server in debug mode: `make run`; Flask will restart if source code is modified

- run tests: `make test` (see also: [Testing Flask Applications](http://flask.pocoo.org/docs/0.12/testing/))

- to remove virtualenv, built distributions, and clean up local deployment: `make clean`

- to add more python dependencies: add to `requirements.txt` and `requirements-workers.txt`

## Configuring Workers

To configure a Cachito Celery worker, create a Python file at `/etc/cachito/celery.py`. Any
variables set in this file will be applied to the Celery worker when running in production mode
(default).

Custom configuration for the Celery workers are listed below:

* `broker_url` - the URL RabbitMQ instance to connect to. See the
  [broker_url](https://docs.celeryproject.org/en/latest/userguide/configuration.html#std:setting-broker_url)
  configuration documentation.
* `cachito_api_url` - the URL to the Cachito API (e.g. `https://cachito-api.domain.local/api/v1/`).
* `cachito_archives_dir` - the directory for long-term storage of app source archives. This
    configuration is required, and the directory must already exist and be writeable.
* `cachito_athens_url` - the URL to the Athens instance to use for caching golang dependencies. This
  is only necessary for workers that process golang requests.
* `cachito_auth_type` - the authentication type to use when accessing protected Cachito API
  endpoints. If this value is `None`, authentication will not be used. This defaults to `kerberos`
  in production.
* `cachito_kerberos_keytab` - the path to the Kerberos keytab file to use for authentication. If
  it's not set, the path in the environment variable `KRB5_CLIENT_KTNAME` is used.
* `cachito_log_level` - the log level to configure the workers with (e.g. `DEBUG`, `INFO`, etc.).
* `cachito_shared_dir` - the directory for short-term storage of bundled source archives. This
    configuration is required, and the directory must already exist and be writeable. The
    underlying volume must also be available in the API.

## Configuring the API

Custom configuration for the API:

* `CACHITO_MAX_PER_PAGE` - the maximum amount of items in a page for paginated results.
* `CACHITO_SHARED_DIR` - the directory for short-term storage of bundled source archives. This
    configuration is required, and the directory must already exist and be writeable. The
    underlying volume must also be available in the workers.
* `CACHITO_WAIT_TIMEOUT` - the timeout used for waiting for a synchronous task to complete.
* `CACHITO_WORKER_USERNAMES` - the list of usernames without the realm that are allowed to
    use the `/requests/<id>` patch endpoint. The workers use this to update the request
    state.
* `LOGIN_DISABLED` - disables authentication requirements.

Additionally, to configure the communication with the Cachito Celery workers, create a Python file
at `/etc/cachito/celery.py`, and set the
[broker_url](https://docs.celeryproject.org/en/latest/userguide/configuration.html#std:setting-broker_url)
configuration to point to your RabbitMQ instance.

If you are planning to deploy Cachito with authentication enabled, you'll need to use
a web server that supplies the `REMOTE_USER` environment variable when the user is
properly authenticated. A common deployment option is using httpd (Apache web server)
with the `mod_auth_gssapi` module.
