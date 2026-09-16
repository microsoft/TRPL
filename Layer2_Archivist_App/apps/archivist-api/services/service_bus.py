# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Simple Azure Service Bus message sender."""

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage, TransportType
from core.config import settings


def send_to_queue(queue_name: str, message: str) -> None:
    """
    Send a message to a Service Bus queue.

    Args:
        queue_name: Name of the queue to send to
        message_data: Dictionary to send as JSON message
    """

    if settings.service_bus_connection_string:
        if settings.environment != "local":
            raise ValueError(
                "AZURE_SERVICEBUS_CONNECTION_STRING is allowed only when "
                "ENVIRONMENT=local; non-local deployments must use managed identity"
            )
        client = ServiceBusClient.from_connection_string(
            settings.service_bus_connection_string
        )
    elif settings.service_bus_fully_qualified_namespace:
        # Zero-trust app integration subnets often block AMQP ports 5671/5672; use 443.
        client = ServiceBusClient(
            fully_qualified_namespace=settings.service_bus_fully_qualified_namespace,
            credential=DefaultAzureCredential(),
            transport_type=TransportType.AmqpOverWebsocket,
        )
    else:
        raise ValueError(
            "Service Bus is not configured: set AZURE_SERVICEBUS_CONNECTION_STRING "
            "for ENVIRONMENT=local or AZURE_SERVICEBUS_FULLY_QUALIFIED_NAMESPACE "
            "for managed identity authentication"
        )
    with client:
        sender = client.get_queue_sender(queue_name)
        with sender:
            servicebus_message = ServiceBusMessage(message)
            sender.send_messages(servicebus_message)
