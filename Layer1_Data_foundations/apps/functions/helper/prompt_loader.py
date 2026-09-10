"""
Prompt loader utility for loading and managing OCR extraction prompts.
"""

import os
from pathlib import Path
from typing import Dict, Optional, List
import logging

logger = logging.getLogger(__name__)

# List of visual resource types that should use visual_description prompt
VISUAL_RESOURCE_TYPES = [
    "other (visual)", "cartoon", "photograph",
    "postcard", "stereograph", "music, sheet",
    "pamphlet", "advertisement", "drawing",
    "map", "envelope", "pin, campaign",
    "scrapbook", "painting", "stamp, postage",
    "medal, commemorative", "poster", "button, political",
    "engraving", "transparency, slide", "pin, clothing",
    "handkerchief", "plaque", "ribbon, commemorative",
    "vial", "coin, commemorative", "plate, commemorative",
    "platter", "case, cigar", "vase",
    "book, comic", "razor", "fan, hand", "decanter",
    "jug, toby", "napkin", "clasp, clothing",
    "bookmark", "desk", "lottery ticket",
    "ornament, christmas tree", "tray", "seal",
    "spoon, demitasse", "ballot",
    "pendant"
]

# Default mapping of resource_type to prompt file names
# Can be overridden via environment variable OCR_PROMPT_MAPPING (JSON format)
DEFAULT_RESOURCE_TYPE_PROMPT_MAPPING = {
    # Default prompt for all resource types unless overridden
    "default": "04_ocr_plus_entity_extraction",
    # Add specific resource type mappings here:
    "visual": "visual_description",
}


class PromptLoader:
    """Utility class for loading prompts from markdown files."""

    def __init__(self, prompts_dir: Optional[str] = None):
        """Initialize the prompt loader.

        Args:
            prompts_dir: Path to prompts directory. Defaults to app/prompts.
        """
        if prompts_dir is None:
            # Default to the 'prompts' subdirectory next to this file
            prompts_dir = Path(__file__).parent / "prompts"

        self.prompts_dir = Path(prompts_dir)
        self._prompts_cache: Dict[str, str] = {}
        self._resource_type_mapping = self._load_resource_type_mapping()

    def _load_resource_type_mapping(self) -> Dict[str, str]:
        """Load resource type to prompt mapping from environment or use defaults."""
        import json
        mapping_json = os.getenv("OCR_PROMPT_MAPPING")
        if mapping_json:
            try:
                custom_mapping = json.loads(mapping_json)
                # Merge with defaults (custom overrides default)
                merged = DEFAULT_RESOURCE_TYPE_PROMPT_MAPPING.copy()
                merged.update(custom_mapping)
                logger.info("Loaded custom OCR prompt mapping: %s", list(custom_mapping.keys()))
                return merged
            except json.JSONDecodeError as e:
                logger.warning("Invalid OCR_PROMPT_MAPPING JSON, using defaults: %s", e)
        return DEFAULT_RESOURCE_TYPE_PROMPT_MAPPING.copy()

    def load_prompt(self, prompt_name: str) -> str:
        """Load a prompt from a markdown file.

        Args:
            prompt_name: Name of the prompt file (without .md extension)

        Returns:
            The prompt content as a string

        Raises:
            FileNotFoundError: If the prompt file doesn't exist
        """
        if prompt_name in self._prompts_cache:
            return self._prompts_cache[prompt_name]

        prompt_file = self.prompts_dir / f"{prompt_name}.md"

        if not prompt_file.exists():
            raise FileNotFoundError(f"Prompt file not found: {prompt_file}")

        with open(prompt_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Cache the loaded prompt
        self._prompts_cache[prompt_name] = content
        return content

    def load_ocr_prompt(self) -> str:
        """Load the OCR extraction prompt."""
        return self.load_prompt("01_ocr_extraction")

    def load_entity_prompt(self) -> str:
        """Load the entity extraction prompt."""
        return self.load_prompt("02_entity_extraction")

    def load_merged_prompt(self) -> str:
        """Load the merged OCR + entity extraction prompt."""
        return self.load_prompt("04_ocr_plus_entity_extraction")

    def load_visual_prompt(self) -> str:
        """Load the visual description extraction prompt."""
        return self.load_prompt("visual_description")

    def load_prompt_for_resource_type(self, resource_type: Optional[str] = None) -> str:
        """Load the appropriate prompt based on resource_type.

        Args:
            resource_type: The resource type (e.g., "letter", "photograph", "document").
                          If None or not found in mapping, uses default prompt.

        Returns:
            The prompt content as a string
        """
        # Normalize resource_type (lowercase, strip whitespace)
        if resource_type:
            resource_type = resource_type.lower().strip()

            # Check if resource_type is a visual type and use mapping
            if resource_type in VISUAL_RESOURCE_TYPES:
                prompt_name = self._resource_type_mapping.get(
                    "visual",
                    "visual_description"
                )
                logger.debug("Loading prompt '%s' for visual resource_type '%s'", prompt_name, resource_type)
                return self.load_prompt(prompt_name)

        # Look up prompt name in mapping
        prompt_name = self._resource_type_mapping.get(
            resource_type,
            self._resource_type_mapping.get("default", "04_ocr_plus_entity_extraction")
        )

        logger.debug("Loading prompt '%s' for resource_type '%s'", prompt_name, resource_type)
        return self.load_prompt(prompt_name)

    def list_available_prompts(self) -> Dict[str, list]:
        """List all available prompts.

        Returns:
            Dictionary with available prompts
        """
        if not self.prompts_dir.exists():
            logger.debug("Prompts directory does not exist: %s", self.prompts_dir)
            return {"core": []}

        prompts: List[str] = []

        for file_path in self.prompts_dir.glob("*.md"):
            if file_path.name == "README.md":
                continue
            prompts.append(file_path.stem)

        return {"core": sorted(prompts)}

    def clear_cache(self) -> None:
        """Clear the prompts cache."""
        self._prompts_cache.clear()
