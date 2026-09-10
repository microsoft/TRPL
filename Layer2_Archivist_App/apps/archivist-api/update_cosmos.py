# pylint: skip-file

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables
load_dotenv()
load_dotenv('.env.local')

# Import shared CosmosClient singleton
from services.cosmos_service import get_cosmos_client

# Use shared singleton client
client = get_cosmos_client()
container = client.get_database_client(os.getenv("COSMOS_DB_DATABASE_NAME")).get_container_client(os.getenv("COSMOS_DB_CONTAINER_NAME"))

for item in container.query_items(
    query="SELECT c.id,c.record_id, c.asset_details FROM c WHERE ARRAY_LENGTH(c.asset_details) > 0",
    enable_cross_partition_query=True
):
    confidences = [
        a.get("ocr_result", {}).get("confidence_scores", {}).get("ocr_confidence")
        for a in item["asset_details"]
        if a.get("ocr_result", {}).get("confidence_scores", {}).get("ocr_confidence") is not None
    ]
    if confidences:
        avg_conf = sum(confidences) / len(confidences)
        print(item)
        container.patch_item(
            item=item["id"],
            partition_key=item["record_id"],
            patch_operations=[
                {"op": "add", "path": "/asset_avg_confidence", "value": avg_conf}
            ]
        )
