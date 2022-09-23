# Tracing

Cachito supports [OpenTelemetry tracing][1].  Internally, various python libraries used have automated tracing available, including [Requests][2], [Flask][3], [SQLAlchemy][4], and [Celery][5].  Spans are created automatically in each component, and the overall trace ID is passed appropriately. 

### Development

The docker-compose.yml file includes the configuration of a jaeger container to collect traces in the development environment.   The local instance of Jaeger is available at [http://localhost:16686][6]

### Deployment configuration

Cachito's tracing is configured via configuration variables in the /etc/cachito/settings.py (CACHITO_OTLP_EXPOTER_ENDPOINT) and /etc/cachito/celery.py (cachito_otlp_exporter_endpoint). 
This should be set to a valid URL that includes the URL and port of a listening OTLP-compatible service. 
If the configuration variable is not defined, trace information will be printed in the log.



[1]: https://opentelemetry.io/docs/concepts/signals/traces/
[2]: https://pypi.org/project/opentelemetry-instrumentation-requests/
[3]: https://pypi.org/project/opentelemetry-instrumentation-flask/
[4]: https://pypi.org/project/opentelemetry-instrumentation-sqlalchemy/
[5]: https://pypi.org/project/opentelemetry-instrumentation-celery/
[6]: https://localhost:16686/
