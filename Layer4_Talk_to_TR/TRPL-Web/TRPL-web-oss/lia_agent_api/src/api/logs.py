# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from contextlib import contextmanager
from datetime import datetime
import logging
import asyncio
from api.config import config

from contextvars import ContextVar


# Context variable to store the current session_id for logging
_session_id_context: ContextVar[str] = ContextVar("session_id", default="")
_node_name_context: ContextVar[str] = ContextVar("node_name", default="")


class AddContextProcessor:
    def on_emit(self, log_data):
        session_id = _session_id_context.get()
        node_name = _node_name_context.get()
        if session_id:
            log_data.log_record.attributes["session_id"] = session_id
        if node_name:
            log_data.log_record.attributes["node_name"] = node_name

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis: int = 30000):
        pass


class AddContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = _session_id_context.get()
        record.node_name = _node_name_context.get()
        return True


def configure_logging():

    handler = logging.StreamHandler()
    handler.addFilter(AddContextFilter())
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        format="%(asctime)s %(levelname)s: [%(node_name)s] %(message)s",
    )

    logging.getLogger("lia.debate").setLevel(logging.DEBUG)
    logging.getLogger("lia.api.routers.debate").setLevel(logging.DEBUG)

    # Quiet down Azure SDK verbose logging
    logging.getLogger("azure").setLevel(logging.WARNING)

    # Initialize Application Insights if connection string is available
    if config.applicationinsights_connection_string:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry._logs import get_logger_provider

        configure_azure_monitor(
            connection_string=config.applicationinsights_connection_string,
        )
        # Retrieve the default LoggerProvider created by the helper
        provider = get_logger_provider()

        # Add your custom processor to it
        provider.add_log_record_processor(AddContextProcessor())


# Context manager to set session_id for logging
@contextmanager
def logging_session_id(session_id: str | None):
    """Context manager to set session_id for logging."""
    _session_id_context.set(session_id)
    try:
        yield
    finally:
        _session_id_context.set(None)


# Context manager to set node name for logging
@contextmanager
def logging_node_name(node_name: str | None):
    """Context manager to set node name for logging."""
    _node_name_context.set(node_name)
    try:
        yield
    finally:
        _node_name_context.set(None)


class QueueHandler(logging.Handler):
    def __init__(self, queue: asyncio.Queue):
        super().__init__()
        self.queue = queue

    def emit(self, record: logging.LogRecord):
        message = {
            "type": "log",
            "timestamp": datetime.now().isoformat(),
            "level": record.levelname.lower(),
            "message": record.msg,
        }
        # only StreamHandlers use Formatters
        if getattr(record, "session_id", None):
            message["session_id"] = record.session_id
        if getattr(record, "node_name", None):
            message["node_name"] = record.node_name
        self.queue.put_nowait(message)
