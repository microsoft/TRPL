# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest

from debate.services.private_config import (
    PrivateConfigError,
    load_private_json,
    load_private_text,
)


def test_load_private_text_from_file(monkeypatch, tmp_path):
    config_file = tmp_path / "guardrail.txt"
    config_file.write_text("deployment-only value", encoding="utf-8")
    monkeypatch.setenv("TEST_PRIVATE_FILE", str(config_file))

    assert (
        load_private_text("TEST_PRIVATE_INLINE", "TEST_PRIVATE_FILE")
        == "deployment-only value"
    )


def test_load_private_json_from_environment(monkeypatch):
    monkeypatch.setenv("TEST_PRIVATE_JSON", json.dumps(["private-value"]))

    assert load_private_json(
        "TEST_PRIVATE_JSON",
        "TEST_PRIVATE_JSON_FILE",
        list,
    ) == ["private-value"]


def test_private_config_rejects_ambiguous_sources(monkeypatch, tmp_path):
    config_file = tmp_path / "guardrail.txt"
    config_file.write_text("file value", encoding="utf-8")
    monkeypatch.setenv("TEST_PRIVATE_INLINE", "inline value")
    monkeypatch.setenv("TEST_PRIVATE_FILE", str(config_file))

    with pytest.raises(PrivateConfigError, match="Set only one"):
        load_private_text("TEST_PRIVATE_INLINE", "TEST_PRIVATE_FILE")


def test_private_config_requires_a_source():
    with pytest.raises(PrivateConfigError, match="Set TEST_MISSING"):
        load_private_text("TEST_MISSING", "TEST_MISSING_FILE")
