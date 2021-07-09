import os
import socket

from prometheus_client import multiprocess
from prometheus_client.core import CollectorRegistry
from prometheus_flask_exporter.multiprocess import GunicornInternalPrometheusMetrics


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
