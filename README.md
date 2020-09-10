# Cachito

Cachito is a service to store (and serve) source code for applications. Upon a request, Cachito
will fetch a specific revision of a given repository from the Internet and store it permanently in
its internal storage. Namely, it stores the source code for a specific Git commit from a given Git
repository, which could be from a forge such as [GitHub](https://github.com) or
[GitLab](https://gitlab.com). This way, even if that repository (or that revision) is deleted, it
is still possible to track the pristine source code for the original sources. In fact, if the
sources have already been previously fetched, Cachito will simply serve the stored copy.

Cachito also supports identifying and permanently storing dependencies for certain package managers
and making them available for building the application. Like it does for source code, future
requests that utilize these same dependencies will be taken from Cachito's internal storage rather
than be fetched from the Internet. See the [Package Manager Feature Support](#feature-support)
section for the package managers that Cachito currently supports.

Cachito will produce bundles as the output artifact of a request. The bundle is a tarball that
contains the source code of the application and all the sources of its dependencies. For some
package managers, these dependencies can be used directly for building the application. Other
package managers will provide an alternative mechanism for this (e.g. a custom npm registry with
the declared npm dependencies). Regardless of if the dependencies in the bundle are used for
building the application, they are always present so that the source of these dependencies
can be published alongside the application for license compliance.

## Table of Contents

* [Coding Standards](#coding-standards)
* [Quick Start](#quick-start)
* [Pre-built Container Images](#pre-built-container-images)
* [Prerequisites](#prerequisites)
* [Development](#development)
* [Database Migrations](#database-migrations)
* [API Documentation](#api-documentation)
* [Configuring Workers](#configuring-workers)
* [Configuring the API](#configuring-the-api)
* [Flags](#flags)
* [Nexus](#nexus)
* [Package Managers](#package-managers)

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

```python
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

```bash
make run
```

Alternatively, you could also run the application with
[podman-compose](https://github.com/containers/podman-compose) by setting the
`CACHITO_COMPOSE_ENGINE` variable (for now SELinux must be set to the
**Permissive** mode before running the make command):

> :warning: **Disabling SELinux or running it in Permissive mode may be
> dangerous. Do it at your own risk and make sure you re-enable it after
> running your integration tests.**

```bash
make run CACHITO_COMPOSE_ENGINE=podman-compose
```

Verify in the browser at [http://localhost:8080/](http://localhost:8080/)

Use curl to make requests:

```bash
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
```

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

```bash
dnf install gcc python3-devel
```

## Development

### Virtualenv

You may create a virtualenv with Cachito and its dependencies installed with the following command:

```bash
make venv
```

This installs Cachito in
[develop mode](http://setuptools.readthedocs.io/en/latest/setuptools.html#development-mode) which
allows modifying the source code directly without needing to reinstall Cachito. This is really
useful for syntax highlighting in your IDE, however, it's not practical to use as a development
environment since Cachito has dependencies on other services.

### docker-compose

You may create and run the containerized development environment with
[docker-compose](https://docs.docker.com/compose/) with the following command:

```bash
make run
```

The will automatically create and run the following containers:

* **athens** - the [Athens](https://docs.gomods.io/) instance responsible for permanently storing
  dependencies for the `gomod` package manager.
* **cachito-api** - the Cachito REST API. This is accessible at
  [http://localhost:8080](http://localhost:8080).
* **cachito-worker** - the Cachito Celery worker. This container is also responsible for configuring
  Nexus at startup.
* **db** - the Postgresql database used by the Cachito REST API.
* **nexus** - the [Sonatype Nexus Repository Manager](https://www.sonatype.com/nexus-repository-oss)
  instance that is responsible for permanently storing dependencies for the `npm` package manager.
  The management UI is accessible at [http://localhost:8082](http://localhost:8082). The username is
  `admin` and the password is `admin`.
* **rabbitmq** - the RabbitMQ instance for communicating between the API and the worker. The
  management UI is accessible at [http://localhost:8081](http://localhost:8081). The username is
  `cachito` and the password is `cachito`.

The REST API and the worker will restart if the source code is modified. Please note that the REST
API may stop restarting if there is a syntax error.

### Unit Tests

To run the unit tests with [tox](https://tox.readthedocs.io/en/latest/), you may run the following
command:

```bash
make test
```

### Integration Tests

To run the integration tests with [tox](https://tox.readthedocs.io/en/latest/), you may run the
following command:

```bash
tox -e integration
```

By default, some tests will require custom configuration and will run against your local development
environment. Read the [integration tests read me](tests/integration/README.md) for more information.

### Clean Up

To remove the virtualenv, built distributions, and the local development environment, you may run
the following command:

```bash
make clean
```

If you are using podman, do not forget to set the `CACHITO_COMPOSE_ENGINE` variable:

```bash
make clean CACHITO_COMPOSE_ENGINE=podman-compose
```

### Adding Dependencies

To add more Python dependencies, add them to the following files:

* [requirements.txt](requirements.txt)
* [requirements-web.txt](requirements-web.txt)

Additionally, please install the corresponding RPMs in the container images at:

* [Dockerfile-api](docker/Dockerfile-api)
* [Dockerfile-workers](docker/Dockerfile-workers)

### Accessing Private Repositories

If your Cachito worker needs to access private repositories in your development environment, you
may mount a
[.netrc](https://www.gnu.org/software/inetutils/manual/html_node/The-_002enetrc-file.html) file
by adding the volume mount `- /path/to/.netrc:/root/.netrc:ro,z` in your `docker-compose.yml`
file under the `cachito-worker` container.

## Database Migrations

Follow the steps below for database data and/or schema migrations:

* Checkout the master branch and ensure no schema changes are present in `cachito/web/models.py`
* Set `SQLALCHEMY_DATABASE_URI` to `sqlite:///cachito-migration.db` in `cachito/web/config.py`
  under the `Config` class
* Run `cachito db upgrade` which will create an empty database in the root of your Git repository
  called `cachito-migration.db` with the current schema applied
* Checkout a new branch where the changes are to be made
* In case of schema changes,
  * Apply any schema changes to `cachito/web/models.py`
  * Run `cachito db migrate` which will autogenerate a migration script in
    `cachito/web/migrations/versions`
* In case of no schema changes,
  * Run `cachito db revision` to create an empty migration script file
* Rename the migration script so that the suffix has a description of the change
* Modify the docstring of the migration script
* For data migrations, define the schema of any tables you will be modifying. This is so that it
  captures the schema of the time of the migration and not necessarily what is in models.py since
  that reflects the latest schema.
* Modify the `upgrade` function to make the adjustments as necessary
* Modify the `downgrade` function to reverse the changes that were made in the `upgrade` function
* Make any adjustments to the migration script as necessary
* To test the migration script,
  * Populate the database with some dummy data as per the requirement
  * Run `cachito db upgrade`
  * Also test the downgrade by running `cachito db downgrade <previous revision>`
    (where previous revision is the revision ID of the previous migration script)
* Remove the configuration of `SQLALCHEMY_DATABASE_URI` that you set earlier
* Remove `cachito-migration.db`
* Commit your changes
* Check "615c19a1cee1_add_npm.py" as an example that does a schema change and a data migration

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
* `cachito_auth_cert` - the SSL certificate to be used for authentication. See
  https://requests.readthedocs.io/en/master/user/advanced/#client-side-certificates for reference on
  how to provide this certificate.
* `cachito_auth_type` - the authentication type to use when accessing protected Cachito API
  endpoints. If this value is `None`, authentication will not be used. This defaults to `kerberos`
  in production. The `cert` value is also valid and would use an SSL certificate for authentication.
  This requires `cachito_auth_cert` to be provided.
* `cachito_bundles_dir` - the directory for storing bundle archives which include the source archive
  and dependencies. This configuration is required, and the directory must already exist and be
  writeable.
* `cachito_default_environment_variables` - a dictionary where the keys are names of package
  managers. The values are dictionaries where the keys are default environment variables to
  set for that package manager and the values are dictionaries with the keys `value` and `kind`. The
  `value` must be a string which specifies the value of the environment variable. The `kind` must
  also be a string which specifies the type of value, either `"path"` or `"literal"`. Check
  `cachito/workers/config.py::Config` for the default value of this configuration.
* `cachito_download_timeout` - the timeout when downloading application source archives from sources
  such as GitHub. The default is `120` seconds.
* `cachito_gomod_ignore_missing_gomod_file` - if `True` and the request specifies the `gomod`
  package manager but there is no `go.mod` file present in the repository, Cachito will skip
  the `gomod` package manager for the request. If `False`, the request will fail if the `go.mod`
  file is missing. This defaults to `False`.
* `cachito_gomod_strict_vendor` - the bool to disable/enable the strict vendor mode. This defaults
  to `False`. For a repo that has gomod dependencies, if the `vendor` directory exists and this config
  option is set to `True`, Cachito will fail the request.
* `cachito_js_download_batch_size` - the number of JavaScript dependencies to download at once using
  `npm pack`. If this value is too high, Nexus will return the error "Header is too large". This
  defaults to `30`.
* `cachito_log_level` - the log level to configure the workers with (e.g. `DEBUG`, `INFO`, etc.).
* `cachito_nexus_ca_cert` - the CA certificate that signed the SSL certificate used by the Nexus
  instance. This defaults to `/etc/cachito/nexus_ca.pem`. If this file does not exist, Cachito will
  not provide the CA certificate in the package manager configuration.
* `cachito_nexus_hoster_password` - the password of the Nexus service account used by Cachito for
  the Nexus instance that has the hosted repositories. This is used instead of
  `cachito_nexus_password` for uploading content if you are using the two Nexus instance approach as
  described in the "Nexus For npm" section. If this is set, `cachito_nexus_hoster_username` must
  also be set.
* `cachito_nexus_hoster_url` - the URL to the Nexus instance that has the hosted repositories. This
  is used instead of `cachito_nexus_url` for uploading content if you are using the two Nexus
  instance approach as described in the "Nexus For npm" section.
* `cachito_nexus_hoster_username` - the username of the Nexus service account used by Cachito for
  the Nexus instance that has the hosted repositories. This is used instead of
  `cachito_nexus_username` for uploading content if you are using the two Nexus instance approach as
  described in the "Nexus For npm" section. If this is set, `cachito_nexus_hoster_password` must
  also be set.
* `cachito_nexus_js_hosted_repo_name` - the name of the Nexus hosted repository for JavaScript
  package managers. This defaults to `cachito-js-hosted`.
* `cachito_nexus_max_search_attempts` - the number of times Cachito will retry searching for non
  PyPI assets in the raw pip repositories to retrieve a URL to append to the requirements file.
* `cachito_nexus_npm_proxy_repo_url` - the URL to the `cachito-js` repository which is a Nexus group
  that points to the `cachito-js-hosted` hosted repository and the `cachito-js-proxy` proxy
  repository. This defaults to `http://localhost:8081/repository/cachito-js/`. This only needs to
  change if you are using the two Nexus instance approach as described in the "Nexus For npm"
  section or you use a different name for the repository.
* `cachito_nexus_password` - the password of the Nexus service account used by Cachito.
* `cachito_nexus_pip_raw_repo_name` - the name of the Nexus raw repository for the `pip` package
  manager. This defaults to `cachito-pip-raw`.
* `cachito_nexus_pypi_proxy_url` - the URL of the Nexus PyPI proxy repository for the `pip` package
  manager. Configured using a full URL rather than just a repo name because we need the additional
  flexibility.
* `cachito_nexus_proxy_password` - the password of the unprivileged user that has read access
  to the main Cachito repositories (e.g. `cachito-js`). This is needed if the Nexus instance that
  hosts the main Cachito repositories has anonymous access disabled. This is the case if Cachito
  utilizes just a single Nexus instance.
* `cachito_nexus_proxy_username` - the username of the unprivileged user that has read access
  to the main Cachito repositories (e.g. `cachito-js`). This is needed if the Nexus instance that
  hosts the main Cachito repositories has anonymous access disabled. This is the case if Cachito
  utilizes just a single Nexus instance.
* `cachito_nexus_request_repo_prefix` - the prefix of Nexus proxy repositories made for each
  request for applicable package managers (e.g. `cachito-npm-1`). This defaults to `cachito-`.
* `cachito_nexus_timeout` - the timeout when making a Nexus API request. The default is `60`
  seconds.
* `cachito_nexus_url` - the base URL to the Nexus Repository Manager 3 instance used by Cachito.
* `cachito_nexus_username` - the username of the Nexus service account used by Cachito. The
  following privileges are required: `nx-repository-admin-*-*-*`, `nx-repository-view-npm-*-*`,
  `nx-roles-all`, `nx-script-*-*`, `nx-users-all` and `nx-userschangepw`. This defaults to
  `cachito`.
* `cachito_npm_file_deps_allowlist` - the npm "file" dependencies that are allowed in the lock file
  for the "npm" package manager. This configuration is a dictionary with the keys as package names
  and the values as lists of dependency names. This defaults to `{}`.
* `cachito_request_file_logs_dir` - the directory to write the request specific log files. If `None`, per
  request log files are not created. This defaults to `None`.
* `cachito_request_file_logs_format` - the format for the log messages of the request specific log files.
  This defaults to `"[%(asctime)s %(name)s %(levelname)s %(module)s.%(funcName)s] %(message)s"`.
* `cachito_request_file_logs_level` - the log level for the request specific log files. This defaults to
  `DEBUG`.
* `cachito_request_file_logs_perm` - the log file permission for the request specific log files. This
  defaults to `0o660`.
* `cachito_request_lifetime` - the number of days before a request that is in the `complete` state
  or that is stuck in the `in_progress` state will be marked as stale by the `cachito-cleanup`
  script. This defaults to `1`.
* `cachito_sources_dir` - the directory for long-term storage of app source archives. This
  configuration is required, and the directory must already exist and be writeable.
* `cachito_task_log_format` - the log format that Celery displays when a task is executing. This
  defaults to
  `"[%(asctime)s #%(request_id)s %(name)s %(levelname)s %(module)s.%(funcName)s] %(message)s"`.

To configure the workers to use a Kerberos keytab for authentication, set the `KRB5_CLIENT_KTNAME`
environment variable to the path of the keytab. Additional Kerberos configuration can be made in
`/etc/krb5.conf`.

## Configuring the API

Custom configuration for the API:

* `CACHITO_BUNDLES_DIR` - the root of the bundles directory that is also accessible by the
  workers. This is used to download the bundle archives created by the workers.
* `CACHITO_DEFAULT_PACKAGE_MANAGERS` - the default package managers to use when no package managers
  are specified on a request. This defaults to `["gomod"]`.
* `CACHITO_MAX_PER_PAGE` - the maximum amount of items in a page for paginated results.
* `CACHITO_PACKAGE_MANAGERS` - the list of enabled package managers. This defaults to `["gomod"]`.
* `CACHITO_REQUEST_FILE_LOGS_DIR` - the directory to load the request specific log files. If `None`, per
  request log files information will not appear in the API response. This defaults to `None`.
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

## Flags

* `gomod-vendor` - the flag to indicate the vendoring requirement for gomod dependencies. If present in the
  Cachito request, Cachito will run `go mod vendor` instead of `go mod download` to gather dependencies.
* `pip-dev-preview` - required for requests that use the `pip` package manager until Cachito support
  for Python proves to be production-ready

## Nexus

### Nexus For npm

The npm package manager functionality relies on [Nexus Repository Manager 3][nexus-docs] to store
npm dependencies. The Nexus instance will have an npm group repository (e.g. `cachito-js`) which
points to an npm hosted repository (e.g. `cachito-js-hosted`) and an npm proxy repository
(e.g. `cachito-js-proxy`) that points to registry.npmjs.org. The hosted repository will contain all
non-registry dependencies and the proxy repository will contain all dependencies from the npm
registry. The union of these two repositories gives the set of all the npm dependencies ever
encountered by Cachito.

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

### Nexus For pip

The pip package manager functionality relies on [Nexus Repository Manager 3][nexus-docs] to store
pip dependencies. The Nexus instance will have a PyPI proxy repository (e.g. `cachito-pip-proxy`)
that points to pypi.org and a raw repository (e.g. `cachito-pip-raw`) which will be used to store
external dependencies. The PyPI proxy repository will cache all PyPI packages that Cachito downloads
through it and the raw repository will hold tarballs or zip archives of external dependencies that
Cachito will upload after fetching them from the original locations.

On each request, Cachito will create a PyPI hosted repository and a raw repository, e.g.
`cachito-pip-hosted-1` and `cachito-pip-raw-1`. Cachito will upload all dependencies for the request
to these repositories (dependencies from PyPI to the hosted repository, external dependencies to the
raw one). Cachito will provide environment variables and configuration files that, when applied
to the user's environment, will allow them to install their dependencies from the above-mentioned
repositories. When installing dependencies from the Cachito-provided repositories, the user is
inherently blocked from installing anything that they did not declare as a dependency, because the
repositories will only contain content that Cachito has made available.

These repositories are created per request and deleted when the request is marked as stale or the
request fails.

### Common Configuration

Refer to the "Configuring Workers" section to see how to configure Cachito to use Nexus. Please
note that you may choose to use two Nexus instances. One for hosting the permanent content and the
other for the ephemeral repositories created per request. This is useful if your organization
already has a shared Nexus instance but doesn't want Cachito to have near admin level access on it.
In this case, you will need to configure the following additional settings that point to the
Nexus instance that hosts the permanent content: `cachito_nexus_hoster_username`,
`cachito_nexus_hoster_password`, and `cachito_nexus_hoster_url`.

## Package Managers

### Feature Support

The table below shows the supported package managers and their support level in Cachito.

Feature                 | gomod | npm | pip |
---                     | ---   | --- | --- |
Baseline                | ✓     | ✓   | ✓   |
Content Manifest        | ✓     | ✓   | ✓   |
Dependency Replacements | ✓     | x   | x   |
Dev Dependencies        | ✓     | ✓   | ✓   |
External Dependencies   | N/A   | ✓   | ✓   |
Multiple Paths          | x     | ✓   | ✓   |
Nested Dependencies     | ✓     | ✓   | x   |
Offline Installations   | ✓     | x   | x   |

#### Feature Definitions

* **Baseline** - The basic requirements are all met and this is ready for production use. This means
  that all dependencies from official sources declared in a lock file will be properly identified
  and shown in the REST API. The dependencies will be permanently stored by Cachito and be reused
  when a future request declares the same dependency. Additionally, Cachito will provide a mechanism
  for the application to be built using just the declared dependencies from Cachito. The dependency
  sources are also included in the bundle generated by Cachito for convenience so that the sources
  can be published alongside of the application for licensing requirements.
* **Content Manifest** - The `/api/<version>/requests/<id>/content-manifest` returns a Content
  Manifest JSON document that describes the application's dependencies and sources.
* **Dependency Replacements** - Dependency replacements can be specified when creating a Cachito
  request. This is a convenient feature to allow dependencies to be swapped without making changes
  in the source repository.
* **Dev Dependencies** - Cachito can distinguish between dependencies used for running the
  application and building/testing the application. For example, for the `npm` package manager, the
  application may require `webpack` to minify their JavaScript and CSS files but that is not
  used at runtime.
* **External Dependencies** - External dependencies are supported such as those not from the default
  registry/package index. For example, for the `npm` package manager, the `package-lock.json` file
  may have a dependency installed directly from GitHub and not from the npm registry.
* **Multiple Paths** - Cachito supports a source repository with multiple applications within it.
  The paths within the source repository are provided by the user when creating the request.
* **Nested Dependencies** - Dependencies that are stored directly in the source Git repository.
  For example, `npm` allows `file` dependencies with the `cachito_npm_file_deps_allowlist`
  configuration. `gomod` allows this through the `go.mod` replace directive.
* **Offline Installations** - The dependencies can be installed solely with the contents of the
  bundle. This is true for the `gomod` package manager, however, the `npm` and `pip` package
  managers rely on Nexus to be online and properly configured by Cachito. If users were so inclined,
  they could find ways to do an offline install for any package manager, but only `gomod` supports
  this out of the box (i.e. the user does not need to change their workflow).

### gomod

The gomod package manager works by parsing the `go.mod` file present in the source repository to
determine which dependencies are required to build the application.

Cachito then downloads the dependencies through [Athens](https://docs.gomods.io/) so that they
are permanently stored and at the same time create a Go module cache to be stored in the request's
bundle.

Cachito will produce a bundle that is downloadable at `/api/v1/requests/<id>/download`. This
bundle will contain the application source code in the `app` directory and Go module cache of all
the dependencies in the `deps/gomod` directory.

Cachito will provide environment variables in the REST API to set for the Go tooling to use this
cache when building the application.

#### Go package level dependencies and the go-package Cachito package type

On top of finding the Go module and its dependencies, and providing their sources and the proper
environment variables for a successful build from such sources, Cachito will also discover the top
level Go packages in the source repository and their (package level) dependencies.

These package level dependencies will be included in the Cachito API request response at the
`/api/v1/requests/<id>` endpoint as packages with the `go-package` type.

Finally, the package level dependencies will be used to compose the Content Manifests shipped at the
`/api/v1/requests/<id>/content-manifest` API endpoint.

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

[nexus-docs]: https://help.sonatype.com/repomanager3
