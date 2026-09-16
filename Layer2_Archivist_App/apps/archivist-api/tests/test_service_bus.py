# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from services import service_bus


def _client_context():
    client = MagicMock()
    client.__enter__.return_value = client
    sender = MagicMock()
    sender.__enter__.return_value = sender
    client.get_queue_sender.return_value = sender
    return client, sender


def test_local_environment_uses_connection_string(monkeypatch):
    client, sender = _client_context()
    monkeypatch.setattr(service_bus.settings, "environment", "local")
    monkeypatch.setattr(
        service_bus.settings,
        "service_bus_connection_string",
        "Endpoint=sb://local-emulator;",
    )
    monkeypatch.setattr(
        service_bus.settings,
        "service_bus_fully_qualified_namespace",
        None,
    )

    with patch.object(
        service_bus.ServiceBusClient,
        "from_connection_string",
        return_value=client,
    ) as from_connection_string:
        service_bus.send_to_queue("queue", '{"message":"test"}')

    from_connection_string.assert_called_once_with("Endpoint=sb://local-emulator;")
    client.get_queue_sender.assert_called_once_with("queue")
    sender.send_messages.assert_called_once()


def test_non_local_environment_rejects_connection_string(monkeypatch):
    monkeypatch.setattr(service_bus.settings, "environment", "production")
    monkeypatch.setattr(
        service_bus.settings,
        "service_bus_connection_string",
        "Endpoint=sb://not-allowed;",
    )

    with pytest.raises(ValueError, match="allowed only"):
        service_bus.send_to_queue("queue", "{}")


def test_cloud_environment_uses_managed_identity(monkeypatch):
    client, sender = _client_context()
    credential = MagicMock()
    monkeypatch.setattr(service_bus.settings, "environment", "production")
    monkeypatch.setattr(service_bus.settings, "service_bus_connection_string", None)
    monkeypatch.setattr(
        service_bus.settings,
        "service_bus_fully_qualified_namespace",
        "example.servicebus.windows.net",
    )

    with (
        patch.object(service_bus, "DefaultAzureCredential", return_value=credential),
        patch.object(service_bus, "ServiceBusClient", return_value=client) as client_class,
    ):
        service_bus.send_to_queue("queue", "{}")

    client_class.assert_called_once_with(
        fully_qualified_namespace="example.servicebus.windows.net",
        credential=credential,
        transport_type=service_bus.TransportType.AmqpOverWebsocket,
    )
    sender.send_messages.assert_called_once()
