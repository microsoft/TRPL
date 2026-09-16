# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Quick script to inspect the actual schema of documents in CosmosDB.
Run this to see what fields your documents actually have.

Usage:
    cd apps/archivist-api
    python tests/inspect_schema.py
"""

import json
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# Load environment variables
load_dotenv()
load_dotenv('.env.local')

# Import shared CosmosClient singleton
from services.cosmos_service import get_cosmos_client

# Get configuration for display
endpoint = os.getenv('COSMOS_DB_ENDPOINT')
database_name = os.getenv('COSMOS_DB_DATABASE_NAME')
container_name = os.getenv('COSMOS_DB_CONTAINER_NAME', 'ocr')

print("=" * 80)
print("CosmosDB Schema Inspector")
print("=" * 80)
print(f"Endpoint: {endpoint}")
print(f"Database: {database_name}")
print(f"Container: {container_name}")
print("=" * 80)

try:
    # Connect to CosmosDB using shared singleton client
    client = get_cosmos_client()
    database = client.get_database_client(database_name)
    container = database.get_container_client(container_name)

    # Query for a few sample documents
    query = "SELECT * FROM c"
    items = list(container.query_items(
        query=query,
        enable_cross_partition_query=True,
        max_item_count=5  # Just get 5 samples
    ))
    if not items:
        print("❌ No documents found in container!")
    else:
        print(f"✅ Found {len(items)} sample documents\n")
        # Collect all unique fields
        all_fields = set()
        for item in items:
            all_fields.update(item.keys())
        print("📋 Fields found in documents:")
        print("-" * 80)
        for field in sorted(all_fields):
            # Count how many documents have this field
            count = sum(1 for item in items if field in item)
            # Get sample value
            sample_item = next((item for item in items if field in item), None)
            sample_value = sample_item.get(field) if sample_item else None
            value_type = type(sample_value).__name__
            # Show if value is large (like nested objects)
            if isinstance(sample_value, dict):
                sample_display = f"{{...}} (dict with {len(sample_value)} keys)"
            elif isinstance(sample_value, list):
                sample_display = f"[...] (list with {len(sample_value)} items)"
            elif isinstance(sample_value, str) and len(sample_value) > 50:
                sample_display = sample_value[:50] + "..."
            else:
                sample_display = sample_value
            presence = f"{count}/{len(items)}" if count < len(items) else "all"
            print(f"  • {field:<30} ({value_type:<10}) - in {presence} docs")
            print(f"    Example: {sample_display}")
            print()

        print("=" * 80)
        print("\n📄 Sample Document (first one):")
        print("-" * 80)
        print(json.dumps(items[0], indent=2, default=str))
        print("=" * 80)

except Exception as error:
    print(f"❌ Error: {str(error)}")
    print("\nMake sure your .env file is configured correctly:")
    print("  COSMOS_DB_ENDPOINT=...")
    print("  Use az login + RBAC (no COSMOS_DB_KEY).")
    print("  COSMOS_DB_DATABASE_NAME=...")
    print("  COSMOS_DB_CONTAINER_NAME=...")
