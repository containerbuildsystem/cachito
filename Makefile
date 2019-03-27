all: run

clean:
	rm -rf venv && rm -rf *.egg-info && rm -rf dist && rm -rf *.log* && rm -rf .tox

venv:
	virtualenv --python=python3 venv && venv/bin/python setup.py develop && venv/bin/pip install -r requirements-dev.txt

run: venv
	FLASK_ENV=development FLASK_APP=cachito/web/wsgi:app venv/bin/flask run

test:
	tox
