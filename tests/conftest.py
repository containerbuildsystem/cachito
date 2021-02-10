# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import os
import tempfile

import git
import pytest
import flask_migrate

from cachito.web import db as _db
from cachito.web.config import TEST_DB_FILE
from cachito.web.app import create_app


@pytest.fixture()
def app(request):
    """Return Flask application for the pytest session."""
    return _make_app(request, "cachito.web.config.TestingConfig")


@pytest.fixture()
def app_no_auth(request):
    """Return Flask application without authentication for the pytest session."""
    return _make_app(request, "cachito.web.config.TestingConfigNoAuth")


def _make_app(request, config):
    """Create an application for the given config name."""
    app = create_app(config)
    # Establish an application context before running the tests. This allows the use of
    # Flask-SQLAlchemy in the test setup.
    ctx = app.app_context()
    ctx.push()

    def teardown():
        ctx.pop()

    request.addfinalizer(teardown)
    return app


@pytest.fixture(scope="session")
def auth_env():
    return {"REMOTE_USER": "tbrady@DOMAIN.LOCAL"}


@pytest.fixture(scope="session")
def auth_ssl_env():
    return {
        "SSL_CLIENT_S_DN": "CN=tbrady,OU=serviceusers,DC=domain,DC=local",
        "SSL_CLIENT_VERIFY": "SUCCESS",
    }


@pytest.fixture()
def client(app):
    """Return Flask application client for the pytest session."""
    return app.test_client()


@pytest.fixture()
def client_no_auth(app_no_auth):
    """Return Flask application client without authentication for the pytest session."""
    return app_no_auth.test_client()


@pytest.fixture()
def db(app, tmpdir):
    """Yield a DB with required app tables but with no records."""
    # Clear the database for each test to ensure tests are idempotent.
    try:
        os.remove(TEST_DB_FILE)
    except FileNotFoundError:
        pass

    with app.app_context():
        flask_migrate.upgrade()

    return _db


@pytest.fixture()
def sample_deps():
    return [
        {
            "name": "github.com/Masterminds/semver",
            "type": "gomod",
            "replaces": None,
            "version": "v1.4.2",
        },
        {"name": "github.com/kr/pretty", "type": "gomod", "replaces": None, "version": "v0.1.0"},
        {"name": "github.com/kr/pty", "type": "gomod", "replaces": None, "version": "v1.1.1"},
        {"name": "github.com/kr/text", "type": "gomod", "replaces": None, "version": "v0.1.0"},
        {
            "name": "github.com/op/go-logging",
            "type": "gomod",
            "replaces": None,
            "version": "v0.0.0-20160315200505-970db520ece7",
        },
        {"name": "github.com/pkg/errors", "type": "gomod", "version": "v1.0.0", "replaces": None},
        {
            "name": "golang.org/x/crypto",
            "type": "gomod",
            "replaces": None,
            "version": "v0.0.0-20190308221718-c2843e01d9a2",
        },
        {
            "name": "golang.org/x/net",
            "type": "gomod",
            "replaces": None,
            "version": "v0.0.0-20190311183353-d8887717615a",
        },
        {
            "name": "golang.org/x/sys",
            "type": "gomod",
            "replaces": None,
            "version": "v0.0.0-20190215142949-d0b11bdaac8a",
        },
        {"name": "golang.org/x/text", "type": "gomod", "replaces": None, "version": "v0.3.0"},
        {
            "name": "golang.org/x/tools",
            "type": "gomod",
            "replaces": None,
            "version": "v0.0.0-20190325161752-5a8dccf5b48a",
        },
        {
            "name": "gopkg.in/check.v1",
            "type": "gomod",
            "replaces": None,
            "version": "v1.0.0-20180628173108-788fd7840127",
        },
        {"name": "gopkg.in/yaml.v2", "type": "gomod", "replaces": None, "version": "v2.2.2"},
        {
            "name": "k8s.io/metrics",
            "type": "gomod",
            "replaces": None,
            "version": "./staging/src/k8s.io/metrics",
        },
    ]


@pytest.fixture()
def sample_pkg_lvl_pkg():
    return {
        "name": "github.com/release-engineering/retrodep/v2",
        "type": "go-package",
        "version": "v2.1.1",
    }


@pytest.fixture()
def sample_pkg_deps():
    return [
        {
            "name": "github.com/Masterminds/semver",
            "type": "go-package",
            "replaces": None,
            "version": "v1.4.2",
        },
        {
            "name": "github.com/op/go-logging",
            "type": "go-package",
            "replaces": None,
            "version": "v0.0.0-20160315200505-970db520ece7",
        },
        {
            "name": "github.com/pkg/errors",
            "type": "go-package",
            "replaces": None,
            "version": "v1.0.0",
        },
        {
            "name": "github.com/release-engineering/retrodep/v2/retrodep/glide",
            "type": "go-package",
            "replaces": None,
            "version": "v2.1.1",
        },
        {
            "name": "github.com/release-engineering/retrodep/v2/retrodep",
            "type": "go-package",
            "replaces": None,
            "version": "v2.1.1",
        },
        {
            "name": "golang.org/x/tools/go/vcs",
            "type": "go-package",
            "replaces": None,
            "version": "v0.0.0-20190325161752-5a8dccf5b48a",
        },
        {"name": "gopkg.in/yaml.v2", "type": "go-package", "replaces": None, "version": "v2.2.2"},
    ]


@pytest.fixture()
def sample_deps_replace(sample_deps):
    # Use a copy in case a test uses both this fixture and the sample_deps fixture
    sample_deps_with_replace = copy.deepcopy(sample_deps)
    sample_deps_with_replace[5]["replaces"] = {
        "name": "github.com/pkg/errors",
        "type": "gomod",
        "version": "v0.9.0",
    }
    return sample_deps_with_replace


@pytest.fixture()
def sample_deps_replace_new_name(sample_deps):
    # Use a copy in case a test uses both this fixture and the sample_deps fixture
    sample_deps_with_replace = copy.deepcopy(sample_deps)
    sample_deps_with_replace[5] = {
        "name": "github.com/pkg/new_errors",
        "type": "gomod",
        "replaces": {"name": "github.com/pkg/errors", "type": "gomod", "version": "v0.9.0"},
        "version": "v1.0.0",
    }
    return sample_deps_with_replace


@pytest.fixture()
def sample_package():
    return {
        "name": "github.com/release-engineering/retrodep/v2",
        "type": "gomod",
        "version": "v2.1.1",
    }


@pytest.fixture()
def sample_env_vars():
    sample = {}
    sample["GOPATH"] = sample["GOCACHE"] = {"value": "deps/gomod", "kind": "path"}
    sample["GOMODCACHE"] = {"value": "deps/gomod/pkg/mod", "kind": "path"}
    return sample


@pytest.fixture(scope="session")
def worker_auth_env():
    return {"REMOTE_USER": "worker@DOMAIN.LOCAL"}


@pytest.fixture()
def fake_repo():
    """
    Create a fake git repository representing a remote resource to be fetched.

    This fixture yields a tuple containing two data. The first one is the
    absolute path to the repository, and the other one is the namespaced
    repository name. Because of the repository is created as a temporary
    directory, the repository name looks like tmp/test-tasks-xxxxx.
    """
    with tempfile.TemporaryDirectory(prefix="test-tasks-") as repo_dir:
        r = git.Repo.init(repo_dir)
        r.git.config("user.name", "tester")
        r.git.config("user.email", "tester@localhost")
        open(os.path.join(repo_dir, "readme.rst"), "w").close()
        r.index.add(["readme.rst"])
        r.index.commit("first commit")
        open(os.path.join(repo_dir, "main.py"), "w").close()
        r.index.add(["main.py"])
        r.index.commit("add main source")
        yield repo_dir, repo_dir.lstrip("/")
