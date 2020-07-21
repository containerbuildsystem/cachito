all: run

clean:
	docker-compose down -v
	rm -rf venv && rm -rf *.egg-info && rm -rf dist && rm -rf *.log* && rm -rf .tox && rm -rf tmp

venv:
	virtualenv --python=python3 venv && venv/bin/python setup.py develop && venv/bin/pip install -r requirements.txt -r requirements-web.txt tox

run:
	docker-compose up

test:
	PATH="$PWD/venv/bin:$PATH" tox
