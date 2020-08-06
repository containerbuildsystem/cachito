all: run

clean:
	docker-compose down -v
	rm -rf venv && rm -rf *.egg-info && rm -rf dist && rm -rf *.log* && rm -rf .tox && rm -rf tmp

venv:
	virtualenv --python=python3 venv && venv/bin/python setup.py develop && venv/bin/pip install -r requirements.txt -r requirements-web.txt tox

run:
	# Manually create this directory to allow the interation tests suite to create a locak git
	# repository in that directory. If this is not done here, docker will create the directory
	# as root and python will not be able to create the local repository there.
	mkdir -p ./tmp/cachito-archives
	docker-compose up

test:
	PATH="$PWD/venv/bin:$PATH" tox
