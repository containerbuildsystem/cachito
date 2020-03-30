# cachito

Caching service for source code

## Coding Standards

The codebase conforms to the style enforced by `flake8` with the following exceptions:
* The maximum line length allowed is 100 characters instead of 80 characters

In addition to `flake8`, docstrings are also enforced by the plugin `flake8-docstrings` with
the following exceptions:
* D100: Missing docstring in public module
* D104: Missing docstring in public package
* D105: Missing docstring in magic method

The format of the docstrings should be in the
[reStructuredText](https://docs.python-guide.org/writing/documentation/#restructuredtext-ref) style
such as:
```
Set the state of the request using the Cachito API.

:param int request_id: the ID of the Cachito request
:param str state: the state to set the Cachito request to
:param str state_reason: the state reason to set the Cachito request to
:return: the updated request
:rtype: dict
:raise CachitoError: if the request to the Cachito API fails
```

Additionally, `black` is used to enforce other coding standards.

To verify that your code meets these standards, you may run `tox -e black,flake8`.

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

## API Documentation

The documentation is generated from the [API specification](cachito/web/static/api_v1.yaml)
written in the OpenAPI 3.0 format.

It is available on [GitHub Pages](https://release-engineering.github.io/cachito) or Cachito's root
URL.

## Configuring Workers

To configure a Cachito Celery worker, create a Python file at `/etc/cachito/celery.py`. Any
variables set in this file will be applied to the Celery worker when running in production mode
(default).

Custom configuration for the Celery workers are listed below:

* `broker_url` - the URL RabbitMQ instance to connect to. See the
  [broker_url](https://docs.celeryproject.org/en/latest/userguide/configuration.html#std:setting-broker_url)
  configuration documentation.
* `cachito_api_url` - the URL to the Cachito API (e.g. `https://cachito-api.domain.local/api/v1/`).
* `cachito_api_timeout` - the timeout when making a Cachito API request. The default is `60`
  seconds.
* `cachito_athens_url` - the URL to the Athens instance to use for caching golang dependencies. This
  is only necessary for workers that process golang requests.
* `cachito_auth_type` - the authentication type to use when accessing protected Cachito API
  endpoints. If this value is `None`, authentication will not be used. This defaults to `kerberos`
  in production.
* `cachito_bundles_dir` - the directory for storing bundle archives which include the source archive
  and dependencies. This configuration is required, and the directory must already exist and be
  writeable.
* `cachito_download_timeout` - the timeout when downloading application source archives from sources
  such as GitHub. The default is `120` seconds.
* `cachito_log_level` - the log level to configure the workers with (e.g. `DEBUG`, `INFO`, etc.).
* `cachito_sources_dir` - the directory for long-term storage of app source archives. This
    configuration is required, and the directory must already exist and be writeable.

To configure the workers to use a Kerberos keytab for authentication, set the `KRB5_CLIENT_KTNAME`
environment variable to the path of the keytab. Additional Kerberos configuration can be made in
`/etc/krb5.conf`.

## Configuring the API

Custom configuration for the API:

* `CACHITO_MAX_PER_PAGE` - the maximum amount of items in a page for paginated results.
* `CACHITO_BUNDLES_DIR` - the root of the bundles directory that is also accessible by the
  workers. This is used to download the bundle archives created by the workers.
* `CACHITO_USER_REPRESENTATIVES` - the list of usernames that are allowed to submit requests on
  behalf of other users.
* `CACHITO_WORKER_USERNAMES` - the list of usernames that are allowed to use the `/requests/<id>`
  PATCH endpoint.
* `LOGIN_DISABLED` - disables authentication requirements.

Additionally, to configure the communication with the Cachito Celery workers, create a Python file
at `/etc/cachito/celery.py`, and set the
[broker_url](https://docs.celeryproject.org/en/latest/userguide/configuration.html#std:setting-broker_url)
configuration to point to your RabbitMQ instance.

If you are planning to deploy Cachito with authentication enabled, you'll need to use
a web server that supplies the `REMOTE_USER` environment variable when the user is
properly authenticated. A common deployment option is using httpd (Apache web server)
with the `mod_auth_gssapi` module.
