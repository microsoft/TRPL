from unittest.mock import MagicMock, patch

from helper.create_epub_index import (
    create_epub_search_index,
    delete_epub_search_index,
)


def test_create_uses_shared_search_credential():
    credential = MagicMock()

    with (
        patch(
            "helper.create_epub_index.get_search_credential",
            return_value=credential,
        ),
        patch("helper.create_epub_index.SearchIndexClient") as client_type,
    ):
        client = client_type.return_value
        client.create_or_update_index.side_effect = lambda index: index

        assert create_epub_search_index(
            endpoint="https://search.example.test",
            index_name="test-epub-index",
        )

    client_type.assert_called_once_with(
        endpoint="https://search.example.test",
        credential=credential,
    )


def test_delete_uses_shared_search_credential():
    credential = MagicMock()

    with (
        patch(
            "helper.create_epub_index.get_search_credential",
            return_value=credential,
        ),
        patch("helper.create_epub_index.SearchIndexClient") as client_type,
    ):
        assert delete_epub_search_index(
            endpoint="https://search.example.test",
            index_name="test-epub-index",
        )

    client_type.assert_called_once_with(
        endpoint="https://search.example.test",
        credential=credential,
    )
    client_type.return_value.delete_index.assert_called_once_with("test-epub-index")
