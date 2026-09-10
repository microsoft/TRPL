# OCR Prompt Library

This directory contains the core prompts for the OCR and entity extraction system used by the TRPL Data Pipeline.

## File Organization

| File | Description |
|------|-------------|
| `01_ocr_extraction.md` | Initial OCR text extraction from images |
| `02_entity_extraction.md` | Entity extraction from OCR text |
| `03_dual_pass_merge.md` | Dual-pass OCR result merging |
| `04_ocr_plus_entity_extraction.md` | Combined OCR and entity extraction |
| `metadata_extr.md` | Metadata extraction from letters |
| `OCR_IMPROVEMENTS_GUIDE.md` | Guide for OCR prompt improvements |

## Usage

Each prompt file contains the system message content for Azure OpenAI. The client code loads these prompts and wraps them in the appropriate API message format.

## Customization

Prompts can be customized by:
1. Modifying the existing prompt files
2. Adjusting confidence thresholds and entity categories
3. Updating the output format specifications