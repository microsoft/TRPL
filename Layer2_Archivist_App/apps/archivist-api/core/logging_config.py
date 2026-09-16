# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Azure Application Insights logging configuration."""
import logging
import sys
from typing import Optional

from core.config import settings


class _RequestIdFilter(logging.Filter):
    """Inject request_id from context for log correlation (see core.request_context)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from core.request_context import get_request_id

            rid = get_request_id()
        except Exception:
            rid = ""
        record.request_id = rid if rid else "-"
        return True


def configure_logging(app_name: str = "archivist-api") -> logging.Logger:
    """
    Configure logging with Azure Application Insights integration.
    
    When APPLICATIONINSIGHTS_CONNECTION_STRING is set, logs will be sent to 
    Azure Application Insights. Otherwise, logs go to console only.
    
    Args:
        app_name: Name of the application for logging context
        
    Returns:
        Configured logger instance
    """
    # Get log level from config
    log_level_str = settings.app_insights_log_level.upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    
    # Configure root logger
    _handler = logging.StreamHandler(sys.stdout)
    _handler.addFilter(_RequestIdFilter())
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] - %(message)s",
        handlers=[_handler],
        force=True,
    )
    
    logger = logging.getLogger(app_name)
    logger.setLevel(log_level)

    _configure_correction_intake_audit_logger(log_level)
    
    # Reduce noise from Azure SDK loggers
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    
    return logger


def _configure_correction_intake_audit_logger(log_level: int) -> None:
    """
    Dedicated logger emitting one JSON object per line (no prefix) for correction intake audit.
    Does not propagate to root to avoid duplicate / mixed-format lines.
    """
    audit = logging.getLogger("correction.intake.audit")
    audit.handlers.clear()
    audit.propagate = False
    audit.setLevel(log_level)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(message)s"))
    audit.addHandler(h)


def configure_azure_monitor(app) -> Optional[bool]:
    """
    Configure Azure Monitor (Application Insights) for the FastAPI app.
    
    This sets up:
    - Automatic request tracing
    - Dependency tracking (HTTP calls, database queries)
    - Exception logging
    - Custom metrics and events
    
    Args:
        app: FastAPI application instance
        
    Returns:
        True if configured successfully, False if skipped, None if failed
    """
    connection_string = settings.applicationinsights_connection_string
    
    if not connection_string:
        logging.getLogger("archivist-api").warning(
            "APPLICATIONINSIGHTS_CONNECTION_STRING not set. "
            "Azure Monitor logging disabled. Logs will only go to console."
        )
        return False
    
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor as azure_configure
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        
        # Configure Azure Monitor with the connection string
        # Note: Not specifying logger_name captures ALL loggers (root logger)
        azure_configure(
            connection_string=connection_string,
            instrumentation_options={
                "azure_sdk": {"enabled": True},
                "fastapi": {"enabled": True},
                "requests": {"enabled": True},
            }
        )
        
        # Instrument FastAPI for automatic request tracing
        FastAPIInstrumentor.instrument_app(app)
        
        # Instrument logging to include trace context
        LoggingInstrumentor().instrument(set_logging_format=True)
        
        logging.getLogger("archivist-api").info(
            "Azure Application Insights configured successfully"
        )
        return True
        
    except ImportError as e:
        logging.getLogger("archivist-api").warning(
            "Azure Monitor packages not installed: %s. "
            "Run: pip install azure-monitor-opentelemetry opentelemetry-instrumentation-fastapi",
            str(e)
        )
        return None
    except Exception as e:
        logging.getLogger("archivist-api").error(
            "Failed to configure Azure Application Insights: %s",
            str(e),
            exc_info=True
        )
        return None


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the given name.
    
    Usage:
        from core.logging_config import get_logger
        logger = get_logger(__name__)
        logger.info("Something happened", extra={"custom_dimension": "value"})
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name)

