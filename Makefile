CACHITO_COMPOSE_ENGINE ?= docker-compose
PYTHON_VERSION_VENV ?= python3.10
TOX_ENVLIST ?= python3.10
TOX_ARGS ?=

PODMAN_COMPOSE_AUTO_URL ?= https://raw.githubusercontent.com/containers/podman-compose/devel/podman_compose.py
PODMAN_COMPOSE_TMP ?= tmp/podman_compose.py

ifeq (podman-compose-auto,$(CACHITO_COMPOSE_ENGINE))
ifeq (,$(wildcard $(PODMAN_COMPOSE_TMP)))
$(shell mkdir -p `dirname $(PODMAN_COMPOSE_TMP)`)
$(shell curl -sL $(PODMAN_COMPOSE_AUTO_URL) -o $(PODMAN_COMPOSE_TMP))
$(shell chmod +x $(PODMAN_COMPOSE_TMP))
endif
override CACHITO_COMPOSE_ENGINE = $(PODMAN_COMPOSE_TMP)
endif

# Older versions of podman-compose do not support deleting volumes via -v
DOWN_HELP := $(shell ${CACHITO_COMPOSE_ENGINE} down --help)
ifeq (,$(findstring volume,$(DOWN_HELP)))
DOWN_OPTS :=
else
DOWN_OPTS := -v
endif

UP_OPTS ?=

all: venv run-start

clean: run-down
	rm -rf venv && rm -rf *.egg-info && rm -rf dist && rm -rf *.log* && rm -rf .tox && rm -rf tmp

.PHONY: venv
venv:
	virtualenv --python=${PYTHON_VERSION_VENV} venv
	venv/bin/pip install --upgrade pip
	venv/bin/pip install -r requirements.txt
	venv/bin/pip install tox
	venv/bin/pip install -e .

# Keep test target for backwards compatibility
test test-unit:
	PATH="${PWD}/venv/bin:${PATH}" tox

test-suite test-tox:
	PATH="${PWD}/venv/bin:${PATH}" tox -e $(TOX_ENVLIST) -- $(TOX_ARGS)

pip-compile: venv/bin/pip-compile
	# --allow-unsafe: we use pkg_resources (provided by setuptools) as a runtime dependency
	venv/bin/pip-compile --allow-unsafe --generate-hashes --output-file=requirements.txt requirements.in
	# --allow-unsafe: requirements-test.in includes requirements.txt
	venv/bin/pip-compile --allow-unsafe --generate-hashes --output-file=requirements-test.txt requirements-test.in

venv/bin/pip-compile:
	venv/bin/pip install pip-tools
