CACHITO_COMPOSE_ENGINE ?= docker-compose
PYTHON_VERSION_VENV ?= python3.8
TOX_ENVLIST ?= py38
TOX_ARGS ?=

# Older versions of podman-compose do not support deleting volumes via -v
DOWN_HELP := $(shell ${CACHITO_COMPOSE_ENGINE} down --help)
ifeq (,$(findstring volume,$(DOWN_HELP)))
DOWN_OPTS :=
else
DOWN_OPTS := -v
endif

all: venv run-start

clean: run-down
	rm -rf venv && rm -rf *.egg-info && rm -rf dist && rm -rf *.log* && rm -rf .tox && rm -rf tmp

.PHONY: venv
venv:
	virtualenv --python=${PYTHON_VERSION_VENV} venv && venv/bin/pip install --upgrade pip && venv/bin/pip install -r requirements.txt -r requirements-web.txt tox && venv/bin/python setup.py develop

# Keep run target for backwards compatibility
run run-start:
	$(CACHITO_COMPOSE_ENGINE) up

run-down run-stop:
	$(CACHITO_COMPOSE_ENGINE) down $(DOWN_OPTS)

run-build run-rebuild: run-down
	$(CACHITO_COMPOSE_ENGINE) build

# stop any containers, rebuild containers, and start it again
run-build-start: run-rebuild run-start

# Keep test target for backwards compatibility
test test-unit:
	PATH="${PWD}/venv/bin:${PATH}" tox

test-integration:
	PATH="${PWD}/venv/bin:${PATH}" tox -e integration

test-suite test-tox:
	PATH="${PWD}/venv/bin:${PATH}" tox -e $(TOX_ENVLIST) -- $(TOX_ARGS)

test-all: test-unit test-integration
