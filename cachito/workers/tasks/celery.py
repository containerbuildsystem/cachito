# SPDX-License-Identifier: GPL-3.0-or-later
import sys

import celery
from celery.signals import celeryd_init, task_postrun, task_prerun, worker_process_init
from opentelemetry import trace
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from cachito.workers.celery_logging import (
    cleanup_task_logging,
    cleanup_task_logging_customization,
    setup_task_logging,
    setup_task_logging_customization,
)
from cachito.workers.config import app, get_worker_config, validate_celery_config  # noqa: F401


def _init_celery_tracing(*args, **kwargs):  # pragma: no cover
    """Initialize OTLP tracing, set the processor & endpoint."""
    CeleryInstrumentor().instrument()
    config = get_worker_config()
    if config.cachito_jaeger_exporter_endpoint:
        jaeger_exporter = JaegerExporter(
            agent_host_name=config.cachito_jaeger_exporter_endpoint,
            agent_port=int(config.cachito_jaeger_exporter_port),
        )
        processor = BatchSpanProcessor(jaeger_exporter)
    elif config.cachito_otlp_exporter_endpoint:
        otlp_exporter = OTLPSpanExporter(endpoint=config.cachito_otlp_exporter_endpoint)
        processor = BatchSpanProcessor(otlp_exporter)
    if config.cachito_otlp_exporter_endpoint or config.cachito_jaeger_exporter_endpoint:
        resource = Resource(attributes={SERVICE_NAME: "cachito-worker"})
        provider = TracerProvider(resource=resource)
        # Useful for debugging trace issues...
        # processor = BatchSpanProcessor(ConsoleSpanExporter())
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)


# Workaround https://github.com/celery/celery/issues/5416
if celery.version_info < (4, 3) and sys.version_info >= (3, 7):  # pragma: no cover
    from re import Pattern

    from celery.app.routes import re as routes_re

    routes_re._pattern_type = Pattern

celeryd_init.connect(validate_celery_config)
task_prerun.connect(setup_task_logging_customization)
task_prerun.connect(setup_task_logging)
task_postrun.connect(cleanup_task_logging_customization)
task_postrun.connect(cleanup_task_logging)
worker_process_init.connect(_init_celery_tracing)
