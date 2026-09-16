# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for fail-clear LiveKit worker deployment configuration."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from env_config import env_bool, required_deployment_value


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_env_bool_accepts_truthy_values(value):
    with patch.dict(os.environ, {"AVATAR_ENABLED": value}, clear=False):
        assert env_bool("AVATAR_ENABLED") is True


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off"])
def test_env_bool_accepts_falsy_values(value):
    with patch.dict(os.environ, {"AVATAR_ENABLED": value}, clear=False):
        assert env_bool("AVATAR_ENABLED", True) is False


def test_env_bool_uses_default_when_unset():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("AVATAR_ENABLED", None)
        assert env_bool("AVATAR_ENABLED", True) is True


def test_env_bool_rejects_ambiguous_values():
    with patch.dict(os.environ, {"AVATAR_ENABLED": "maybe"}, clear=False):
        with pytest.raises(RuntimeError, match="must be one of"):
            env_bool("AVATAR_ENABLED")


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        " ",
        "<your-model-id>",
        "your-model-id",
        "replace-me",
        "@Microsoft.KeyVault(SecretUri=...)",
    ],
)
def test_required_deployment_value_rejects_missing_or_placeholder_values(value):
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LEMONSLICE_MODEL", None)
        if value is not None:
            os.environ["LEMONSLICE_MODEL"] = value

        with pytest.raises(RuntimeError, match="concrete deployment value"):
            required_deployment_value("LEMONSLICE_MODEL")


def test_required_deployment_value_returns_concrete_value():
    with patch.dict(
        os.environ,
        {"LEMONSLICE_MODEL": "model-123"},
        clear=False,
    ):
        assert required_deployment_value("LEMONSLICE_MODEL") == "model-123"
