# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Service Bus helper utilities.

Provides helpers to send messages to Azure Service Bus with scheduling
that adapts to message index (no queue-depth reads — avoids requiring
``Azure Service Bus Data Receiver`` on the Function managed identity).

The Function App MI is granted **Azure Service Bus Data Sender** in
the deployment templates. There is no queue trigger
yet; wire ``send_message`` from ``function_app.py`` when scheduled enqueue is required.
"""



from datetime import datetime, timedelta, timezone
import os
import random

from azure.servicebus import ServiceBusClient, ServiceBusMessage

from .credential import get_credential


def _resolve_fqdn() -> str:
    """Return the Service Bus fully-qualified namespace from env."""
    fqdn = os.getenv("AZURE_SERVICEBUS_FQDN")
    if not fqdn:
        raise ValueError(
            "Missing required environment variable: AZURE_SERVICEBUS_FQDN "
            "(e.g. <namespace>.servicebus.windows.net)"
        )
    return fqdn


def send_message(message: str | bytes, queue_name: str, index: int = 0):
    """Send a message to the given Service Bus queue.

    Authenticates with the Function App's managed identity. The Function MI
    must hold ``Azure Service Bus Data Sender`` on the namespace — granted in
    the deployment templates.

    Args:
        message: Message payload (str or bytes).
        queue_name: Destination queue name.
        index: Optional per-call index to space messages.
    """

    connection_string = os.getenv("AZURE_SERVICEBUS_CONNECTION_STRING", "").strip()
    if connection_string:
        if os.getenv("ENVIRONMENT", "production").strip().lower() != "local":
            raise RuntimeError(
                "AZURE_SERVICEBUS_CONNECTION_STRING is only supported when ENVIRONMENT=local"
            )
        servicebus_client = ServiceBusClient.from_connection_string(connection_string)
    else:
        servicebus_client = ServiceBusClient(
            fully_qualified_namespace=_resolve_fqdn(),
            credential=get_credential(),
        )

    with servicebus_client:
        with servicebus_client.get_queue_sender(queue_name) as sender:
            scheduled_time = compute_scheduled_time(index, active_count=0)
            servicebus_message = ServiceBusMessage(
                message, scheduled_enqueue_time_utc=scheduled_time
            )
            sender.send_messages(servicebus_message)


def compute_scheduled_time(
    index: int,
    base_delay=2,
    per_item_delay=0.5,
    jitter=0.2,
    active_count=0,
    max_safe=500,
):
    """Compute a scheduled enqueue time for a message.

    Uses index, per-item delay, optional load factor from ``active_count``,
    and jitter. ``active_count`` is reserved for future use (queue depth was
    removed to allow Sender-only RBAC).
    """
    load_factor = min(active_count / max_safe, 1.0)
    dynamic_delay = (
        base_delay
        + (index * per_item_delay)
        + (load_factor * 3.0)
        + random.uniform(0, jitter)
    )
    return datetime.now(timezone.utc) + timedelta(seconds=dynamic_delay)
