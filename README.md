# cachito

(Experimental) Caching service for source code

## Quick Start

Run the application:

    make run

And open it in the browser at [http://127.0.0.1:5000/](http://127.0.0.1:5000/)

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

- to remove virtualenv and built distributions: `make clean`

- to add more python dependencies: add to `requirements.txt` and `requirements-workers.txt`

## Configuring Workers

To configure a Cachito Celery worker, create a Python file at `/etc/cachito/celery.py`. Any
variables set in this file will be applied to the Celery worker when running in production mode
(default).

Custom configuration for the Celery workers are listed below:

* `cachito_archives_dir` - the directory for long-term storage of app source archives. This
    configuration is required, and the directory must already exist and be writeable.
* `cachito_shared_dir` - the directory for short-term storage of bundled source archives. This
    configuration is required, and the directory must already exist and be writeable. The
    underlying volume must also be available in the API.

## Configuring the API

Custom configuration for the API:

* `CACHITO_WAIT_TIMEOUT` - the timeout used for waiting for a synchronous task to complete.
* `CACHITO_SHARED_DIR` - the directory for short-term storage of bundled source archives. This
    configuration is required, and the directory must already exist and be writeable. The
    underlying volume must also be available in the workers.
