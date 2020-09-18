CACHITO_COMPOSE_ENGINE ?= docker-compose
ifeq ($(CACHITO_COMPOSE_ENGINE), docker-compose)
	DOWN_OPTS=-v
endif

all: run

clean:
	$(CACHITO_COMPOSE_ENGINE) down $(DOWN_OPTS)
	rm -rf venv && rm -rf *.egg-info && rm -rf dist && rm -rf *.log* && rm -rf .tox && rm -rf tmp

venv:
	virtualenv --python=python3 venv && venv/bin/python setup.py develop && venv/bin/pip install -r requirements.txt -r requirements-web.txt tox

run:
ifeq ($(CACHITO_COMPOSE_ENGINE), podman-compose)
	# SELinux should not be enabled
	@test "$(shell getenforce)" = "Enforcing" && { echo "SELinux is enforcing. Disable it before running with podman-compose (RE-ENABLE IT AFTERWARDS)"; exit 1; } || true
	# Create the other directories for podman-compose compatibility
	# See https://github.com/containers/podman-compose/issues/185
	mkdir -p ./tmp/athens-storage
	mkdir -p ./tmp/request-logs-volume
endif
	# Manually create this directory to allow the integration tests suite to create a local git
	# repository in that directory. If this is not done here, docker will create the directory
	# as root and python will not be able to create the local repository there.
	mkdir -p ./tmp/cachito-archives
	# Manually create the Nexus volume directory and allow others to write in it. The Nexus
	# container runs as the "nexus" user, neither docker nor podman are able to change the
	# owner of a host directory.
	mkdir -p ./tmp/nexus-volume
	setfacl -d -m other::rwx ./tmp/nexus-volume
	setfacl -m other::rwx ./tmp/nexus-volume
	$(CACHITO_COMPOSE_ENGINE) up

test:
	PATH="$PWD/venv/bin:$PATH" tox
