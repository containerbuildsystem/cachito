import os
import socket

from prometheus_client import Gauge, Summary, multiprocess
from prometheus_client.core import CollectorRegistry
from prometheus_flask_exporter.multiprocess import GunicornInternalPrometheusMetrics

cachito_metrics = {}


def init_metrics(app):
    """
    Initialize the Prometheus Flask Exporter.

    :return: a Prometheus Flash Metrics object
    :rtype: PrometheusMetrics
    """
    registry = CollectorRegistry()
    multiproc_temp_dir = app.config["PROMETHEUS_METRICS_TEMP_DIR"]
    hostname = socket.gethostname()

    if not os.path.isdir(multiproc_temp_dir):
        os.makedirs(multiproc_temp_dir)
    multiprocess.MultiProcessCollector(registry, path=multiproc_temp_dir)
    metrics = GunicornInternalPrometheusMetrics.for_app_factory(
        default_labels={"host": hostname}, group_by="endpoint", defaults_prefix="cachito_flask"
    )
    metrics.init_app(app)
    gauge_state = Gauge(
        "cachito_requests_count", "Requests in each state", ["state"], multiprocess_mode="livesum"
    )
    request_duration = Summary(
        "cachito_request_duration_seconds", "Time spent in in_progress state"
    )
    cachito_metrics["gauge_state"] = gauge_state
    cachito_metrics["request_duration"] = request_duration
