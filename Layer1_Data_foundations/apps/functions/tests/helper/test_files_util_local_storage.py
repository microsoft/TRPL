"""Tests for authenticated local Blob downloads during file conversion."""

import io
from types import SimpleNamespace

from PIL import Image

from helper import files_util


class _Downloader:
    def __init__(self, content):
        self._content = content

    def readall(self):
        return self._content


class _BlobClient:
    def __init__(self, content):
        self._content = content

    def get_blob_properties(self):
        return SimpleNamespace(size=len(self._content))

    def download_blob(self):
        return _Downloader(self._content)


def test_conversion_uses_authenticated_blob_client_locally(monkeypatch):
    image_buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(image_buffer, format="PNG")
    blob_client = _BlobClient(image_buffer.getvalue())

    monkeypatch.setattr(files_util, "uses_local_storage", lambda: True)
    monkeypatch.setattr(
        files_util, "get_blob_client_from_url", lambda _: blob_client
    )
    monkeypatch.setattr(
        files_util.requests,
        "head",
        lambda *_, **__: (_ for _ in ()).throw(
            AssertionError("HTTP HEAD should not be used")
        ),
    )
    monkeypatch.setattr(
        files_util.requests,
        "get",
        lambda *_, **__: (_ for _ in ()).throw(
            AssertionError("HTTP GET should not be used")
        ),
    )

    results = files_util.convert_file_url_to_jpeg_data_urls(
        "http://azurite:10000/devstoreaccount1/content-assets/test.png"
    )

    assert len(results) == 1
    assert results[0].startswith("data:image/jpeg;base64,")
