"""
EPUB Parser Service

Extracts structured content from EPUB files including:
- Prologue
- Chapters (with titles and content)
- Epilogue
- Additional sections (foreword, afterword, etc.)

Outputs structured JSON format.
"""

import json
import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any

from ebooklib import epub, ITEM_DOCUMENT

logger = logging.getLogger(__name__)


class HTMLTextExtractor:
    """Extract plain text from HTML using regex (no HTMLParser dependency issues)."""

    def __init__(self, skip_captions: bool = True):
        self._skip_captions = skip_captions

    def extract(self, html: str) -> str:
        """Extract plain text from HTML."""
        if not html:
            return ""

        text = html

        # Remove script and style tags with content
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)

        # Remove figure/figcaption if skipping captions
        if self._skip_captions:
            flags = re.DOTALL | re.IGNORECASE
            text = re.sub(r'<figure[^>]*>.*?</figure>', '', text, flags=flags)
            text = re.sub(r'<figcaption[^>]*>.*?</figcaption>', '', text, flags=flags)

        # Replace block tags with newlines
        text = re.sub(r'<(?:p|div|br|h[1-6]|li|tr)[^>]*>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</(?:p|div|h[1-6]|li|tr)>', '\n', text, flags=re.IGNORECASE)

        # Remove all remaining HTML tags
        text = re.sub(r'<[^>]+>', '', text)

        # Decode HTML entities
        text = text.replace('&nbsp;', ' ')
        text = text.replace('&amp;', '&')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        text = text.replace('&quot;', '"')
        text = text.replace('&#39;', "'")

        # Normalize whitespace
        text = re.sub(r'\n\s*\n+', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)

        return text.strip()


@dataclass
class Section:
    """Represents a section of the book."""
    type: str
    title: str
    content: str
    order: int
    html_content: Optional[str] = None
    word_count: int = 0

    def __post_init__(self):
        if self.content:
            self.word_count = len(self.content.split())


@dataclass
class BookStructure:
    """Represents the complete structure of an EPUB book."""
    title: str
    author: str
    language: str = ""
    publisher: str = ""
    description: str = ""
    prologue: Optional[Section] = None
    chapters: List[Section] = field(default_factory=list)
    epilogue: Optional[Section] = None
    foreword: Optional[Section] = None
    afterword: Optional[Section] = None
    introduction: Optional[Section] = None
    other_sections: List[Section] = field(default_factory=list)
    total_word_count: int = 0

    def to_dict(self, include_html: bool = False, content_as_html: bool = False) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            'metadata': {
                'title': self.title,
                'author': self.author,
                'language': self.language,
                'publisher': self.publisher,
                'description': self.description,
                'totalWordCount': self.total_word_count,
                'chapterCount': len(self.chapters),
                'contentFormat': 'html' if content_as_html else 'text'
            },
            'structure': {
                'hasPrologue': self.prologue is not None,
                'hasEpilogue': self.epilogue is not None,
                'hasForeword': self.foreword is not None,
                'hasAfterword': self.afterword is not None,
                'hasIntroduction': self.introduction is not None,
            }
        }

        def section_to_dict(section: Optional[Section]) -> Optional[Dict]:
            if section is None:
                return None
            d = {
                'title': section.title,
                'content': section.html_content if content_as_html else section.content,
                'wordCount': section.word_count,
                'order': section.order
            }
            if include_html and section.html_content and not content_as_html:
                d['htmlContent'] = section.html_content
            return d

        if self.foreword:
            result['foreword'] = section_to_dict(self.foreword)
        if self.introduction:
            result['introduction'] = section_to_dict(self.introduction)
        if self.prologue:
            result['prologue'] = section_to_dict(self.prologue)

        result['chapters'] = [section_to_dict(ch) for ch in self.chapters]

        if self.epilogue:
            result['epilogue'] = section_to_dict(self.epilogue)
        if self.afterword:
            result['afterword'] = section_to_dict(self.afterword)

        if self.other_sections:
            result['otherSections'] = [section_to_dict(s) for s in self.other_sections]

        return result

    def to_json(
        self, include_html: bool = False, content_as_html: bool = False, indent: int = 2
    ) -> str:
        """Convert to JSON string."""
        data = self.to_dict(include_html, content_as_html)
        return json.dumps(data, indent=indent, ensure_ascii=False)


class EpubParser:
    """Parser for extracting structured content from EPUB files."""

    PROLOGUE_PATTERNS = [
        r'^prologue$', r'^prol[oó]go$', r'^prelude$', r'^before\s+the\s+story',
        r'prologue', r'^pr[oó]logo$'
    ]

    EPILOGUE_PATTERNS = [
        r'^epilogue$', r'^ep[ií]logo$', r'^afterword$', r'^conclusion$',
        r'epilogue', r'^ep[ií]logo$', r'^the\s+end$'
    ]

    CHAPTER_PATTERNS = [
        r'^chapter\s*(\d+|[ivxlcdm]+)',
        r'^ch\.?\s*(\d+|[ivxlcdm]+)',
        r'^(\d+)\.?\s*$',
        r'^part\s*(\d+|[ivxlcdm]+)',
        r'^cap[ií]tulo\s*(\d+|[ivxlcdm]+)',
        r'^chapitre\s*(\d+|[ivxlcdm]+)',
        r'^kapitel\s*(\d+|[ivxlcdm]+)',
    ]

    FOREWORD_PATTERNS = [
        r'^foreword$', r'^pr[oó]logo\s+del\s+autor$', r'^author\'?s?\s+note$',
        r'^preface$', r'^prefacio$'
    ]

    AFTERWORD_PATTERNS = [
        r'^afterword$', r'^author\'?s?\s+note$', r'^about\s+the\s+author$',
        r'^acknowledgements?$', r'^agradecimientos$'
    ]

    INTRODUCTION_PATTERNS = [
        r'^introduction$', r'^introducci[oó]n$', r'^intro$'
    ]

    SKIP_PATTERNS = [
        r'^cover$', r'^title\s*page$', r'^copyright$', r'^dedication$',
        r'^table\s+of\s+contents$', r'^contents$', r'^toc$', r'^index$',
        r'^bibliography$', r'^notes$', r'^endnotes$', r'^footnotes$',
        r'^about\s+this\s+e?-?book$', r'^colophon$', r'^imprint$'
    ]

    def __init__(self, min_chapter_words: int = 100, skip_captions: bool = True):
        self.min_chapter_words = min_chapter_words
        self.skip_captions = skip_captions
        self._text_extractor = HTMLTextExtractor(skip_captions=skip_captions)

    def _matches_pattern(self, text: str, patterns: List[str]) -> bool:
        """Check if text matches any of the given patterns."""
        text_clean = text.strip().lower()
        for pattern in patterns:
            if re.search(pattern, text_clean, re.IGNORECASE):
                return True
        return False

    def _identify_section_type(self, title: str) -> str:
        """Identify the type of section based on its title."""
        if self._matches_pattern(title, self.SKIP_PATTERNS):
            return 'skip'
        if self._matches_pattern(title, self.PROLOGUE_PATTERNS):
            return 'prologue'
        if self._matches_pattern(title, self.EPILOGUE_PATTERNS):
            return 'epilogue'
        if self._matches_pattern(title, self.FOREWORD_PATTERNS):
            return 'foreword'
        if self._matches_pattern(title, self.AFTERWORD_PATTERNS):
            return 'afterword'
        if self._matches_pattern(title, self.INTRODUCTION_PATTERNS):
            return 'introduction'
        if self._matches_pattern(title, self.CHAPTER_PATTERNS):
            return 'chapter'
        return 'other'

    def _extract_title_from_html(self, html_content: str) -> str:
        """Extract title from HTML content."""
        # Try to find h1, h2, or h3 tags
        for tag in ['h1', 'h2', 'h3']:
            pattern = rf'<{tag}[^>]*>(.*?)</{tag}>'
            match = re.search(pattern, html_content, re.IGNORECASE | re.DOTALL)
            if match:
                title = re.sub(r'<[^>]+>', '', match.group(1)).strip()
                if title:
                    return title
        return "Untitled Section"

    def _clean_html(self, html: str) -> str:
        """Clean HTML content for storage."""
        # Remove excessive whitespace
        html = re.sub(r'\s+', ' ', html)
        return html.strip()

    def parse(self, file_path: str, content_as_html: bool = False) -> BookStructure:
        """Parse EPUB file and extract structured content."""
        # Note: content_as_html parameter reserved for future use
        _ = content_as_html
        try:
            book = epub.read_epub(file_path)
        except Exception as e:
            logger.exception("Failed to read EPUB file: %s", e)
            raise ValueError(f"Failed to read EPUB file: {e}") from e

        # Extract metadata
        title = book.get_metadata('DC', 'title')
        title = title[0][0] if title else "Unknown Title"

        author = book.get_metadata('DC', 'creator')
        author = author[0][0] if author else "Unknown Author"

        language = book.get_metadata('DC', 'language')
        language = language[0][0] if language else ""

        publisher = book.get_metadata('DC', 'publisher')
        publisher = publisher[0][0] if publisher else ""

        description = book.get_metadata('DC', 'description')
        description = description[0][0] if description else ""

        structure = BookStructure(
            title=title,
            author=author,
            language=language,
            publisher=publisher,
            description=description
        )

        # Process document items
        order = 0
        for item in book.get_items_of_type(ITEM_DOCUMENT):
            try:
                content = item.get_content().decode('utf-8', errors='ignore')
            except Exception as e:
                logger.warning("Failed to decode item content: %s", e)
                continue

            html_content = self._clean_html(content)
            text_content = self._text_extractor.extract(content)

            if not text_content or len(text_content.split()) < 10:
                continue

            item_title = self._extract_title_from_html(content)
            section_type = self._identify_section_type(item_title)

            if section_type == 'skip':
                continue

            section = Section(
                type=section_type,
                title=item_title,
                content=text_content,
                html_content=html_content,
                order=order
            )
            order += 1

            if section_type == 'prologue' and structure.prologue is None:
                structure.prologue = section
            elif section_type == 'epilogue' and structure.epilogue is None:
                structure.epilogue = section
            elif section_type == 'foreword' and structure.foreword is None:
                structure.foreword = section
            elif section_type == 'afterword' and structure.afterword is None:
                structure.afterword = section
            elif section_type == 'introduction' and structure.introduction is None:
                structure.introduction = section
            elif section_type == 'chapter' or (
                section_type == 'other' and section.word_count >= self.min_chapter_words
            ):
                section.type = 'chapter'
                structure.chapters.append(section)
            else:
                structure.other_sections.append(section)

        # Calculate total word count
        total = 0
        if structure.prologue:
            total += structure.prologue.word_count
        if structure.epilogue:
            total += structure.epilogue.word_count
        if structure.foreword:
            total += structure.foreword.word_count
        if structure.afterword:
            total += structure.afterword.word_count
        if structure.introduction:
            total += structure.introduction.word_count
        for ch in structure.chapters:
            total += ch.word_count
        for s in structure.other_sections:
            total += s.word_count

        structure.total_word_count = total

        return structure


def parse_epub_file(file_content: bytes, content_as_html: bool = False) -> Dict[str, Any]:
    """
    Parse EPUB file from bytes content.
    
    Args:
        file_content: EPUB file content as bytes
        content_as_html: If True, return content as HTML instead of plain text

    Returns:
        Dictionary with parsed book structure
    """
    with tempfile.NamedTemporaryFile(suffix='.epub', delete=False) as tmp_file:
        tmp_file.write(file_content)
        tmp_path = tmp_file.name

    try:
        parser = EpubParser()
        structure = parser.parse(tmp_path, content_as_html=content_as_html)
        return structure.to_dict(include_html=True, content_as_html=content_as_html)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
