#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Verify normal Archivist API operations traverse both Layer 2 queue handlers."""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPOSITORY_ROOT / "Layer1_Data_foundations" / ".env.local"
API_BASE_URL = "http://127.0.0.1:8000/api/v1"


def _compose(env_file: Path, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "--profile",
        "full-stack",
        *arguments,
    ]


def _wait_for_url(url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status < 400:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def _request_json(
    method: str,
    path: str,
    *,
    payload: Any = None,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{API_BASE_URL}{path}",
        data=data,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{method} {path} returned HTTP {exc.code}: {detail[:500]}"
        ) from exc
    if not body:
        return {}
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{method} {path} returned a non-object response")
    return parsed


def _get_optional(path: str) -> dict[str, Any] | None:
    request = urllib.request.Request(f"{API_BASE_URL}{path}", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            parsed = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GET {path} returned HTTP {exc.code}: {detail[:500]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"GET {path} returned a non-object response")
    return parsed


def _wait_for_status(
    path: str,
    expected: set[str],
    timeout: float,
    *,
    fail_fast: set[str] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_status = "missing"
    while time.monotonic() < deadline:
        document = _get_optional(path)
        if document is not None:
            last_status = str(
                document.get("status")
                or document.get("archivist_status")
                or "unknown"
            ).lower()
            if last_status in expected:
                return document
            if fail_fast and last_status in fail_fast:
                raise RuntimeError(
                    f"{path} entered {last_status}: "
                    f"{document.get('error_message') or document.get('archivist_error_message')}"
                )
        time.sleep(2)
    raise RuntimeError(
        f"Timed out waiting for {path} status in {sorted(expected)}; "
        f"last status was {last_status}"
    )


def _wait_for_absence(path: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _get_optional(path) is None:
            return
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {path} to be deleted")


def _run_api_container_script(
    env_file: Path,
    script: str,
    *arguments: str,
) -> None:
    result = subprocess.run(
        _compose(
            env_file,
            "exec",
            "-T",
            "layer2-api",
            "python",
            "-c",
            script,
            *arguments,
        ),
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Layer 2 API fixture command failed: {detail}")


def _create_data_ingestion_fixture(
    env_file: Path,
    source_document_id: str,
    probe_document_id: str,
) -> None:
    script = """
import copy
import sys
from services.cosmos_service import get_cosmos_service

source_id, probe_id = sys.argv[1:3]
service = get_cosmos_service()
source = service.get_document_raw(source_id, source_id)
if not source:
    raise RuntimeError(f"Source document not found: {source_id}")
probe = copy.deepcopy(source)
for key in ("_etag", "_rid", "_self", "_attachments", "_ts"):
    probe.pop(key, None)
probe["id"] = probe_id
probe["record_id"] = probe_id
probe["archivist_status"] = "reviewed"
probe["archivist_error_message"] = None
probe["asset_details"] = []
probe["asset_count"] = 0
metadata = probe.setdefault("metadata", {})
if isinstance(metadata, dict):
    metadata["Title"] = "Local queue acceptance fixture"
service.get_container().upsert_item(probe)
"""
    _run_api_container_script(
        env_file,
        script,
        source_document_id,
        probe_document_id,
    )


def _delete_data_ingestion_fixture(env_file: Path, probe_document_id: str) -> None:
    script = """
import sys
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from services.cosmos_service import get_cosmos_service

probe_id = sys.argv[1]
try:
    get_cosmos_service().get_container().delete_item(
        item=probe_id,
        partition_key=probe_id,
    )
except CosmosResourceNotFoundError:
    pass
"""
    _run_api_container_script(env_file, script, probe_document_id)


def _verify_data_ingestion_queue(
    env_file: Path,
    source_document_id: str,
    timeout: float,
) -> None:
    probe_document_id = f"queue-acceptance-{uuid.uuid4().hex}"
    path = f"/documents/{probe_document_id}"
    _create_data_ingestion_fixture(
        env_file,
        source_document_id,
        probe_document_id,
    )
    try:
        _request_json(
            "PATCH",
            path,
            payload={"archivist_status": "publishing"},
        )
        failed = _wait_for_status(path, {"failed"}, timeout)
        if failed.get("archivist_error_message") != "No asset_details found in document":
            raise RuntimeError(
                "Data ingestion handler did not produce the expected fixture failure"
            )
    finally:
        _delete_data_ingestion_fixture(env_file, probe_document_id)


def _build_epub() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">layer3-acceptance</dc:identifier>
    <dc:title>The Juniper Basin Field Notes</dc:title>
    <dc:creator>TRPL Acceptance Test</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>
""",
        )
        archive.writestr(
            "OEBPS/nav.xhtml",
            """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Contents</title></head>
  <body>
    <nav epub:type="toc"><ol><li><a href="chapter.xhtml">Chapter</a></li></ol></nav>
  </body>
</html>
""",
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Chapter</title></head>
  <body>
    <h1>The Blue Heron Compact</h1>
    <p>This fictional acceptance-test account describes a 1908 conservation
    meeting where President Theodore Roosevelt named the imaginary Juniper
    Basin a protected reserve. In the story, he called the agreement the
    Blue Heron Compact after reviewing reports about the basin's forests.</p>
  </body>
</html>
""",
        )
    return output.getvalue()


def _multipart_epub(filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = f"----trpl-{uuid.uuid4().hex}"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\n'
            ).encode(),
            b"Content-Type: application/epub+zip\r\n\r\n",
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return body, f"multipart/form-data; boundary={boundary}"


def _section_selections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selections: list[dict[str, Any]] = []
    for section in sections:
        if "order" in section:
            selections.append({"order": section["order"], "selected": True})
        children = section.get("children")
        if isinstance(children, list):
            selections.extend(_section_selections(children))
    return selections


def _delete_epub(document_id: str, timeout: float) -> None:
    path = f"/epub/documents/{document_id}"
    if _get_optional(path) is None:
        return
    _request_json("DELETE", path)
    _wait_for_absence(path, timeout)


def _verify_epub_queue(timeout: float, *, retain: bool = False) -> str | None:
    filename = f"queue-acceptance-{uuid.uuid4().hex}.epub"
    body, content_type = _multipart_epub(filename, _build_epub())
    uploaded = _request_json(
        "POST",
        "/epub/upload",
        data=body,
        headers={"Content-Type": content_type},
    )
    document_id = str(uploaded.get("id") or "")
    if not document_id:
        raise RuntimeError("EPUB upload did not return a document ID")
    path = f"/epub/documents/{document_id}"
    cleanup = True
    try:
        parsed = _wait_for_status(path, {"parsed"}, timeout, fail_fast={"failed"})
        sections = parsed.get("sections")
        if not isinstance(sections, list):
            raise RuntimeError("Parsed EPUB did not expose sections")
        selections = _section_selections(sections)
        if not selections:
            raise RuntimeError("Parsed EPUB did not contain selectable sections")
        _request_json(
            "PATCH",
            f"{path}/sections",
            payload={"sections": selections},
        )

        _request_json("POST", f"{path}/extract")
        _wait_for_status(path, {"validate"}, timeout, fail_fast={"failed"})

        _request_json("POST", f"{path}/approve")
        terminal = _wait_for_status(path, {"completed", "failed", "error"}, timeout)
        terminal_status = str(terminal.get("status", "")).lower()
        if terminal_status != "completed":
            detail = terminal.get("error_message") or "no error detail was recorded"
            raise RuntimeError(f"EPUB ingestion entered {terminal_status}: {detail}")

        if retain:
            cleanup = False
            return document_id
        return None
    finally:
        if cleanup and _get_optional(path) is not None:
            _delete_epub(document_id, timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument(
        "--document-id",
        default="synthetic-record-001",
        help="Existing local Layer 1 record used as a temporary fixture template.",
    )
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument(
        "--retain-epub",
        action="store_true",
        help="Keep the completed EPUB fixture and print its document ID.",
    )
    args = parser.parse_args()

    env_file = args.env_file.resolve()
    if not env_file.is_file():
        raise RuntimeError(
            f"Missing {env_file}; run Layer1_Data_foundations/scripts/local_dev.py "
            "configure first"
        )

    _wait_for_url("http://127.0.0.1:8000/health", args.timeout)
    _wait_for_url("http://127.0.0.1:5300/health", args.timeout)
    _verify_data_ingestion_queue(env_file, args.document_id, args.timeout)
    epub_document_id = _verify_epub_queue(
        args.timeout,
        retain=args.retain_epub,
    )
    print(
        "Layer 2 queue flow verified through normal individual-document and "
        f"EPUB parse/extract/ingest/{'retain' if args.retain_epub else 'delete'} "
        "API operations."
    )
    if epub_document_id:
        print(f"Retained EPUB document ID: {epub_document_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
