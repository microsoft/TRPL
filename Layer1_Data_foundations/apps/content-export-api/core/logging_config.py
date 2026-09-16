# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Logging and Azure Monitor configuration."""
import logging
import sys
from typing import Optional

from core.config import settings


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from core.request_context import get_request_id

            rid = get_request_id()
        except Exception:
            rid = ""
        record.request_id = rid if rid else "-"
        return True


def configure_logging(app_name: str = "content-export-api") -> logging.Logger:
    log_level_str = settings.app_insights_log_level.upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] - %(message)s",
        handlers=[handler],
        force=True,
    )

    logger = logging.getLogger(app_name)
    logger.setLevel(log_level)

    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logger


def configure_azure_monitor(app) -> Optional[bool]:
    connection_string = settings.applicationinsights_connection_string
    if not connection_string:
        logging.getLogger("content-export-api").warning(
            "APPLICATIONINSIGHTS_CONNECTION_STRING not set; Azure Monitor disabled."
        )
        return False

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor as azure_configure
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        azure_configure(
            connection_string=connection_string,
            instrumentation_options={
                "azure_sdk": {"enabled": True},
                "fastapi": {"enabled": True},
                "requests": {"enabled": True},
            },
        )
        FastAPIInstrumentor.instrument_app(app)
        LoggingInstrumentor().instrument(set_logging_format=True)
        return True
    except ImportError:
        return None
    except Exception:
        logging.getLogger("content-export-api").exception(
            "Failed to configure Azure Application Insights"
        )
        return None


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
