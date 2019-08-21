all: run

clean:
	rm -rf venv && rm -rf *.egg-info && rm -rf dist && rm -rf *.log* && rm -rf .tox && rm -rf tmp
	docker-compose down

venv:
	virtualenv --python=python3 venv && venv/bin/python setup.py develop && venv/bin/pip install -r requirements-dev.txt

run:
	docker-compose up

test:
	tox
