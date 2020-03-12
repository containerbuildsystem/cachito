all: run

clean:
	rm -rf venv && rm -rf *.egg-info && rm -rf dist && rm -rf *.log* && rm -rf .tox && rm -rf tmp
	docker-compose down

venv:
	virtualenv --python=python3 venv && venv/bin/python setup.py develop && venv/bin/pip install -r requirements-dev.txt

run:
	docker-compose up

proxy_setup:
	curl -u admin:admin123 -X POST --header 'Content-Type: application/json' http://localhost:8881/service/rest/v1/script -d @nexus.json
	curl -u admin:admin123 -X POST --header 'Content-Type: text/plain' --header "accept: application/json" http://localhost:8881/service/rest/v1/script/proxy/run

test:
	tox
