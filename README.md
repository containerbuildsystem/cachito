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
* `cachito_athens_url` - the URL to the Athens instance to use for caching gomod dependencies. This
  is only necessary for workers that process gomod requests.
* `cachito_auth_type` - the authentication type to use when accessing protected Cachito API
  endpoints. If this value is `None`, authentication will not be used. This defaults to `kerberos`
  in production.
* `cachito_bundles_dir` - the directory for storing bundle archives which include the source archive
  and dependencies. This configuration is required, and the directory must already exist and be
  writeable.
* `cachito_download_timeout` - the timeout when downloading application source archives from sources
  such as GitHub. The default is `120` seconds.
* `cachito_js_download_batch_size` - the number of JavaScript dependencies to download at once using
  `npm pack`. If this value is too high, Nexus will return the error "Header is too large". This
  defaults to `30`.
* `cachito_log_level` - the log level to configure the workers with (e.g. `DEBUG`, `INFO`, etc.).
* `cachito_nexus_ca_cert` - the CA certificate that signed the SSL certificate used by the Nexus
  instance. This defaults to `/etc/cachito/nexus_ca.pem`. If this file does not exist, Cachito will
  not provide the CA certificate in the package manager configuration.
* `cachito_nexus_password` - the password of the Nexus service account used by Cachito.
* `cachito_nexus_timeout` - the timeout when making a Nexus API request. The default is `60`
  seconds.
* `cachito_nexus_unprivileged_password` - the password of the unprivileged user that has read access
  to the main Cachito repositories (e.g. `cachito-js`).
* `cachito_nexus_unprivileged_username` - the username of the unprivileged user that has read access
  to the main Cachito repositories (e.g. `cachito-js`). This defaults to `cachito_unprivileged`.
* `cachito_nexus_url` - the base URL to the Nexus Repository Manager 3 instance used by Cachito.
* `cachito_nexus_username` - the username of the Nexus service account used by Cachito. The
  following privileges are required: `nx-repository-admin-*-*-*`, `nx-repository-view-npm-*-*`,
  `nx-roles-all`, `nx-script-*-*`, `nx-users-all` and `nx-userschangepw`. This defaults to
  `cachito`.
* `cachito_request_lifetime` - the number of days before a request that is in the `complete` state
  or that is stuck in the `in_progress` state will be marked as stale by the `cachito-cleanup`
  script. This defaults to `1`.
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
* `CACHITO_PACKAGE_MANAGERS` - the list of enabled package managers. This defaults to `["gomod"]`.
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

## Nexus

### Nexus For npm

The npm package manager functionality relies on
[Nexus Repository Manager 3](https://help.sonatype.com/repomanager3) to store npm dependencies. The
Nexus instance will have an npm group repository (e.g. `cachito-js`) which points to an npm hosted
repository (e.g. `cachito-js-hosted`) and an npm proxy repository (e.g. `cachito-js-proxy`) that
points to registry.npmjs.org. The hosted repository will contain all non-registry dependencies and
the proxy repository will contain all dependencies from the npm registry. The union of these two
repositories gives the set of all the npm dependencies ever encountered by Cachito.

On each request, Cachito will create a proxy repository to the npm group repository
(e.g. `cachito-js`). Cachito will populate this proxy repository to contain the subset of
dependencies declared in the repository's lock file. Once populated, Cachito will block the
repository from getting additional content. This prevents the consumer of the repository from
installing something that was not declared in the lock file. This is further enforced by locking
down the repository to a single user created for the request, which the consumer will use. Please
keep in mind that for this to function properly, anonymous access needs to be disabled on the Nexus
instance or at least not set to have read access on all repositories.

These repositories and users created per request are deleted when the request is marked as stale
or the request fails.

Refer to the Configuring Workers section to see how to configure Cachito to use Nexus.

## Package Managers

### npm

The npm package manager works by parsing the `npm-shrinkwrap.json` or `package-lock.json` file
present in the source repository to determine what dependencies are required to build the
application.

Cachito then creates an npm registry in an instance of Nexus it manages that contains just
the dependencies discovered in the lock file. The registry is locked down so that no other
dependencies can be added. The connection information is stored in an
[.npmrc](https://docs.npmjs.com/configuring-npm/npmrc.html) file accessible at the
`/api/v1/requests/<id>/configuration-files` API endpoint.

Cachito will produce a bundle that is downloadable at `/api/v1/requests/<id>/download`. This
bundle will contain the application source code in the `app` directory and individual tarballs
of all the dependencies in the `deps/npm` directory. These tarballs are not meant to be used to
build the application. They are there for convenience so that the dependency sources can be
published alongside your application sources. In addition, they can be used to populate a local npm
registry in the event that the application needs to be built without Cachito and the Nexus instance
it manages.

Cachito can also handle dependencies that are not from the npm registry such as those directly
from GitHub, a Git repository, or an HTTP(S) URL. Please note that if the dependency is from a
private repository, set the
[.netrc](https://www.gnu.org/software/inetutils/manual/html_node/The-_002enetrc-file.html) and
`known_hosts` files for the Cachito workers. If the dependency location is not supported, Cachito
will fail the request. When Cachito encounters a supported location, it will download the
dependency, modify the version in the [package.json](https://docs.npmjs.com/files/package.json) to
be unique, upload it to Nexus, modify the top level project's
[package.json](https://docs.npmjs.com/files/package.json) and lock files to use the dependency from
Nexus instead. The modified files will be accessible at the
`/api/v1/requests/<id>/configuration-files` API endpoint. If Cachito encounters this same dependency
again in a future request, it will use it directly from Nexus rather than downloading it and
uploading it again. This guarantees that any dependency used for a Cachito request can be used again
in a future Cachito request.
