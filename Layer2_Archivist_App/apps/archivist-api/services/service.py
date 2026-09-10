"""Base service class for Azure services."""
import sys
from pathlib import Path
from typing import List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.schemas import Item, ItemCreate, ItemResponse


class ItemService:
    """Service for managing items."""

    def __init__(self):
        """Initialize the item service."""
        self._items = []
        self._next_id = 1

    def create_item(self, item_data: ItemCreate) -> ItemResponse:
        """Create a new item.

        Args:
            item_data: Data for the new item

        Returns:
            Response with created item details
        """
        item = Item(item_id=self._next_id, name=item_data.name)
        self._items.append(item)
        self._next_id += 1
        return ItemResponse(item_id=item.item_id, name=item.name)

    def get_item(self, item_id: int) -> Optional[Item]:
        """Get an item by ID.

        Args:
            item_id: ID of the item to retrieve

        Returns:
            Item if found, None otherwise
        """
        for item in self._items:
            if item.item_id == item_id:
                return item
        return None

    def list_items(self) -> List[Item]:
        """Get all items.

        Returns:
            List of all items
        """
        return self._items
