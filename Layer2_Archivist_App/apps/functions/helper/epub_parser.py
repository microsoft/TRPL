"""
EPUB Parser

Extracts structured content from EPUB files including:
- Prologue
- Chapters (with titles and content)
- Epilogue
- Additional sections (foreword, afterword, etc.)

Outputs structured JSON format.
"""

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Union, BinaryIO, List, Dict, Any
from html.parser import HTMLParser

from ebooklib import epub, ITEM_DOCUMENT  # pylint: disable=import-error

logger = logging.getLogger(__name__)


class TocEntry:
    """Represents a table of contents entry with optional nested children."""
    def __init__(self, title: str, href: str, children: List['TocEntry'] = None):
        self.title = title
        self.href = href  # Link to content (e.g., "chapter1.xhtml#section2")
        self.children = children or []
    
    def __repr__(self):
        return f"TocEntry('{self.title}', '{self.href}', children={len(self.children)})"


def load_filter_config() -> Dict[str, Any]:
    """Load filter configuration from JSON file."""
    config_path = Path(__file__).parent / 'epub_filter_config.json'
    default_config = {
        'exclude_tags': ['script', 'style', 'head', 'meta', 'link', 'figcaption', 
                         'figure', 'caption', 'img', 'nav', 'aside'],
        'exclude_classes': ['caption', 'image-caption', 'sidebar', 'footnote'],
        'exclude_ids': ['toc', 'navigation'],
        'exclude_data_attributes': {},
        'class_patterns': ['caption', 'sidebar', 'ad-', 'footnote']
    }
    
    try:
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # Remove notes field if present (documentation only)
                config.pop('notes', None)
                config.pop('description', None)
                return config
    except Exception as e:
        logger.warning("Failed to load filter config, using defaults: %s", e)
    
    return default_config


# Global filter config (loaded once)
_FILTER_CONFIG: Optional[Dict[str, Any]] = None


def get_filter_config() -> Dict[str, Any]:
    """Get filter config (cached)."""
    global _FILTER_CONFIG  # pylint: disable=global-statement
    if _FILTER_CONFIG is None:
        _FILTER_CONFIG = load_filter_config()
        logger.info("Loaded EPUB filter config: %d tags, %d classes, %d patterns excluded",
                   len(_FILTER_CONFIG.get('exclude_tags', [])),
                   len(_FILTER_CONFIG.get('exclude_classes', [])),
                   len(_FILTER_CONFIG.get('class_patterns', [])))
    return _FILTER_CONFIG


def normalize_first_word_caps(text: str) -> str:
    """
    Normalize paragraphs where leading words are in ALL CAPS (drop cap style).
    
    Converts consecutive ALL CAPS words at the start to normal case:
    - First word: first letter uppercase, rest lowercase
    - Other leading caps words: all lowercase
    - Preserves standalone "I" as uppercase (the pronoun)
    
    Example: "HE RIDES OUT to hunt with Kermi" -> "He rides out to hunt with Kermi"
    
    Skips paragraphs that are entirely ALL CAPS (headings).
    
    Args:
        text: The text to normalize
        
    Returns:
        Text with normalized capitalization
    """
    if not text:
        return text
    
    # Split into paragraphs (double newline separated)
    paragraphs = text.split('\n\n')
    normalized = []
    
    for para in paragraphs:
        para_stripped = para.strip()
        if not para_stripped:
            normalized.append(para)
            continue
        
        # Split into words
        words = para_stripped.split()
        if len(words) < 2:
            # Single word paragraph - keep as is
            normalized.append(para)
            continue
        
        # Check if entire paragraph is ALL CAPS - skip (heading)
        all_alpha = ''.join(c for c in para_stripped if c.isalpha())
        if all_alpha and all_alpha.isupper():
            normalized.append(para)
            continue
        
        # Find consecutive ALL CAPS words at start
        # Words with 2+ letters that are all uppercase, or single uppercase letters
        caps_end_idx = 0
        for i, word in enumerate(words):
            word_alpha = ''.join(c for c in word if c.isalpha())
            if word_alpha and word_alpha.isupper():
                caps_end_idx = i + 1
            else:
                break
        
        # Need at least one caps word with 2+ letters to trigger conversion
        has_multi_letter_caps = False
        for i in range(caps_end_idx):
            word_alpha = ''.join(c for c in words[i] if c.isalpha())
            if len(word_alpha) >= 2:
                has_multi_letter_caps = True
                break
        
        if caps_end_idx == 0 or not has_multi_letter_caps:
            # No leading caps words worth converting
            normalized.append(para)
            continue
        
        # Convert leading caps words
        new_words = []
        for i, word in enumerate(words):
            if i < caps_end_idx:
                # In the caps sequence
                if i == 0:
                    # First word: first letter up, rest lower
                    if len(word) == 1:
                        new_words.append(word.upper())
                    else:
                        new_words.append(word[0].upper() + word[1:].lower())
                elif word.upper() == 'I' and len(word) == 1:
                    # Preserve standalone "I" (the pronoun)
                    new_words.append('I')
                else:
                    # Other caps words: lowercase
                    new_words.append(word.lower())
            else:
                # After caps sequence: unchanged
                new_words.append(word)
        
        normalized_para = ' '.join(new_words)
        
        # Preserve leading whitespace from original
        leading_ws = len(para) - len(para.lstrip())
        if leading_ws > 0:
            normalized_para = para[:leading_ws] + normalized_para
        
        normalized.append(normalized_para)
    
    return '\n\n'.join(normalized)


class HTMLTextExtractor(HTMLParser):
    """Extract plain text from HTML content with configurable filtering."""

    def __init__(self, apply_filters: bool = True, custom_config: Optional[Dict] = None):
        super().__init__()
        self.text_parts: List[str] = []
        self._apply_filters = apply_filters
        self._current_skip = False
        self._skip_depth = 0  # Track nested skip tags
        self._block_tags = {'p', 'div', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr'}
        
        # Load filter configuration
        config = custom_config or get_filter_config()
        self._exclude_tags = set(t.lower() for t in config.get('exclude_tags', []))
        self._exclude_classes = set(c.lower() for c in config.get('exclude_classes', []))
        self._exclude_ids = set(i.lower() for i in config.get('exclude_ids', []))
        self._class_patterns = config.get('class_patterns', [])
        self._exclude_data_attrs = config.get('exclude_data_attributes', {})

    def _should_exclude(self, tag: str, attrs: List[tuple]) -> bool:
        """Check if element should be excluded based on config."""
        if not self._apply_filters:
            return False
            
        tag_lower = tag.lower()
        
        # Check tag name
        if tag_lower in self._exclude_tags:
            return True
        
        # Check attributes
        for attr, value in attrs:
            if not value:
                continue
            attr_lower = attr.lower()
            value_lower = value.lower()
            
            # Check class attribute
            if attr_lower == 'class':
                classes = value_lower.split()
                # Exact class match
                if any(cls in self._exclude_classes for cls in classes):
                    return True
                # Pattern match (e.g., 'ad-' matches 'ad-banner')
                for pattern in self._class_patterns:
                    if any(pattern.lower() in cls for cls in classes):
                        return True
            
            # Check id attribute
            elif attr_lower == 'id':
                if value_lower in self._exclude_ids:
                    return True
            
            # Check data-* and role attributes
            elif attr_lower in self._exclude_data_attrs:
                excluded_values = self._exclude_data_attrs[attr_lower]
                if value_lower in [v.lower() for v in excluded_values]:
                    return True
        
        return False

    def handle_starttag(self, tag: str, attrs):
        tag_lower = tag.lower()
        
        if self._should_exclude(tag, attrs) or self._skip_depth > 0:
            self._skip_depth += 1
            self._current_skip = True
        elif tag_lower in self._block_tags:
            self.text_parts.append('\n')

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        
        if self._skip_depth > 0:
            self._skip_depth -= 1
            if self._skip_depth == 0:
                self._current_skip = False
        elif tag_lower in self._block_tags:
            self.text_parts.append('\n')

    def handle_data(self, data: str):
        if not self._current_skip:
            self.text_parts.append(data)

    def get_text(self) -> str:
        text = ''.join(self.text_parts)
        # Normalize whitespace
        text = re.sub(r'\n\s*\n+', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = text.strip()
        # Normalize paragraphs where only first word is ALL CAPS (drop cap style)
        text = normalize_first_word_caps(text)
        return text


@dataclass
class CreatorInfo:
    """Author/contributor information with sorting and role."""
    name: str
    file_as: str = ""  # Sort name (e.g., "Morris, Edmund")
    role: str = ""  # Role code (e.g., "aut" for author)
    
    def to_dict(self) -> Dict[str, str]:
        return {
            'name': self.name,
            'fileAs': self.file_as,
            'role': self.role
        }


@dataclass
class EpubMetadata:
    """Comprehensive metadata from EPUB OPF file."""
    # Dublin Core metadata
    title: str = ""
    title_sort: str = ""  # Sort title (from calibre)
    creator: str = ""  # Primary author name
    creator_info: Optional[CreatorInfo] = None  # Full author info
    contributors: List[CreatorInfo] = field(default_factory=list)  # Contributors with roles
    publisher: str = ""
    language: str = ""
    description: str = ""
    subject: List[str] = field(default_factory=list)  # Categories/tags
    rights: str = ""  # Copyright info
    date: str = ""  # Publication date
    identifier: str = ""  # Primary identifier
    source: str = ""  # Original source
    relation: str = ""  # Related resources
    coverage: str = ""  # Spatial/temporal coverage
    type: str = ""  # Resource type
    format: str = ""  # File format
    
    # Extended identifiers
    isbn: str = ""
    asin: str = ""  # Amazon identifier
    uuid: str = ""
    google_id: str = ""
    calibre_id: str = ""
    
    # Series info (from calibre)
    series: str = ""
    series_index: str = ""
    
    # Calibre metadata
    calibre_timestamp: str = ""
    
    # All identifiers (scheme -> value)
    identifiers: Dict[str, str] = field(default_factory=dict)
    
    # Custom metadata (key-value pairs)
    custom: Dict[str, str] = field(default_factory=dict)
    
    # File info
    opf_version: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            'title': self.title,
            'titleSort': self.title_sort,
            'author': self.creator,  # Alias for compatibility
            'creator': self.creator,
            'creatorInfo': self.creator_info.to_dict() if self.creator_info else None,
            'contributors': [c.to_dict() for c in self.contributors],
            'publisher': self.publisher,
            'language': self.language,
            'description': self.description,
            'subjects': self.subject,
            'rights': self.rights,
            'date': self.date,
            'identifier': self.identifier,
            'source': self.source,
            'relation': self.relation,
            'coverage': self.coverage,
            'type': self.type,
            'format': self.format,
            'series': self.series,
            'seriesIndex': self.series_index,
            'isbn': self.isbn,
            'asin': self.asin,
            'uuid': self.uuid,
            'googleId': self.google_id,
            'calibreId': self.calibre_id,
            'calibreTimestamp': self.calibre_timestamp,
            'identifiers': self.identifiers,
            'opfVersion': self.opf_version,
        }
        if self.custom:
            result['custom'] = self.custom
        return result


@dataclass
class Section:
    """Represents a section of the book."""
    type: str  # 'prologue', 'chapter', 'epilogue', 'foreword', 'afterword', 'introduction', 'other'
    title: str
    content: str
    order: int
    html_content: Optional[str] = None
    word_count: int = 0
    level: int = 1  # Heading level (1 = top, 2 = subsection, etc.)
    parent_order: Optional[int] = None  # Order of parent section (for nested)
    children: List['Section'] = field(default_factory=list)  # Nested sub-sections
    href: str = ""  # Link to content (for lazy loading)
    
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
    epub_metadata: Optional[EpubMetadata] = None  # Full OPF metadata
    
    def to_dict(self, include_html: bool = False, content_as_html: bool = False) -> Dict[str, Any]:
        """
        Convert to dictionary for JSON serialization.
        
        Args:
            include_html: If True, includes htmlContent field alongside content.
            content_as_html: If True, the content field contains HTML instead of plain text.
        """
        # Use full metadata if available, otherwise basic fields
        if self.epub_metadata:
            metadata_dict = self.epub_metadata.to_dict()
        else:
            metadata_dict = {
                'title': self.title,
                'author': self.author,
                'language': self.language,
                'publisher': self.publisher,
                'description': self.description,
            }
        
        result = {
            'metadata': {
                **metadata_dict,
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
                'order': section.order,
                'level': section.level,
                'parentOrder': section.parent_order,
                'href': section.href
            }
            if include_html and section.html_content and not content_as_html:
                d['htmlContent'] = section.html_content
            # ALWAYS include children key (even if empty)
            d['children'] = [section_to_dict(child) for child in section.children] if section.children else []
            return d
        
        if self.foreword:
            result['foreword'] = section_to_dict(self.foreword)
        if self.introduction:
            result['introduction'] = section_to_dict(self.introduction)
        if self.prologue:
            result['prologue'] = section_to_dict(self.prologue)
        
        chapters_dicts = []
        for ch in self.chapters:
            ch_dict = section_to_dict(ch)
            if ch_dict:
                chapters_dicts.append(ch_dict)
                if ch_dict.get('children'):
                    logger.debug("to_dict: Chapter '%s' has %d children in dict",
                                ch_dict.get('title', '')[:30], len(ch_dict['children']))
        result['chapters'] = chapters_dicts
        
        if self.epilogue:
            result['epilogue'] = section_to_dict(self.epilogue)
        if self.afterword:
            result['afterword'] = section_to_dict(self.afterword)
        
        if self.other_sections:
            result['otherSections'] = [section_to_dict(s) for s in self.other_sections]
        
        return result
    
    def to_json(self, include_html: bool = False, content_as_html: bool = False, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(include_html, content_as_html), indent=indent, ensure_ascii=False)


class EpubParser:
    """Parser for extracting structured content from EPUB files."""
    
    # Patterns for identifying section types
    PROLOGUE_PATTERNS = [
        r'^prologue$', r'^prol[oó]go$', r'^prelude$', r'^before\s+the\s+story',
        r'prologue', r'^pr[oó]logo$'
    ]
    
    EPILOGUE_PATTERNS = [
        r'^epilogue$', r'^ep[ií]logo$', r'^afterword$', r'^conclusion$',
        r'epilogue', r'^ep[ií]logo$', r'^the\s+end$'
    ]
    
    CHAPTER_PATTERNS = [
        r'^chapter\s*(\d+|[ivxlcdm]+)',  # Chapter 1, Chapter I
        r'^ch\.?\s*(\d+|[ivxlcdm]+)',     # Ch. 1, Ch 1
        r'^(\d+)\.?\s*[-–—:]?\s*\w',      # "1. Title" or "1 - Title" or just "1"
        r'^(\d+)\.?\s*$',                  # Just number: 1, 1.
        r'^part\s*(\d+|[ivxlcdm]+)',       # Part 1
        r'^section\s*(\d+|[ivxlcdm]+)',    # Section 1
        r'^cap[ií]tulo\s*(\d+|[ivxlcdm]+)', # Spanish: Capítulo
        r'^chapitre\s*(\d+|[ivxlcdm]+)',   # French: Chapitre
        r'^kapitel\s*(\d+|[ivxlcdm]+)',    # German: Kapitel
        r'^book\s*(\d+|[ivxlcdm]+)',       # Book 1
        r'^act\s*(\d+|[ivxlcdm]+)',        # Act 1
        r'^scene\s*(\d+|[ivxlcdm]+)',      # Scene 1
        r'chapter\s*\d+',                   # Contains "chapter" + number anywhere
        r'^[ivxlcdm]+\.?\s*[-–—:]?\s*\w',  # Roman numerals: I. Title, II - Title
        r'^[ivxlcdm]+\.?\s*$',              # Just roman numeral: I, II, III
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
    
    # Patterns to skip (only navigation/metadata pages, not actual content)
    # Be conservative - only skip things that definitely aren't content
    SKIP_PATTERNS = [
        r'^cover$',
        r'^title\s*page$',
        r'^table\s+of\s+contents$', r'^contents$', r'^toc$',
        r'^about\s+this\s+e?-?book$',
        r'^colophon$',
        r'^imprint$',
        r'^half\s*title$',
        r'^also\s+by',  # "Also by this author" pages
        r'^other\s+books\s+by',
    ]
    
    def __init__(self, min_chapter_words: int = 100, skip_captions: bool = True,
                 use_toc: bool = True):
        """
        Initialize the parser.
        
        Args:
            min_chapter_words: Minimum word count to consider content as a chapter.
            skip_captions: Whether to skip image captions (figcaption, figure, etc.).
            use_toc: Whether to use EPUB's table of contents for section detection.
        """
        self.min_chapter_words = min_chapter_words
        self.skip_captions = skip_captions
        self.use_toc = use_toc
    
    def _extract_text(self, html_content: str) -> str:
        """Extract plain text from HTML, applying configured filters."""
        parser = HTMLTextExtractor(apply_filters=self.skip_captions)
        try:
            parser.feed(html_content)
            return parser.get_text()
        except Exception as e:
            logger.warning("HTML parsing error: %s", e)
            # Fallback: simple tag stripping
            text = re.sub(r'<[^>]+>', ' ', html_content)
            return re.sub(r'\s+', ' ', text).strip()

    def _extract_opf_metadata(self, book) -> EpubMetadata:
        """
        Extract comprehensive metadata from EPUB's OPF file.
        
        Captures all Dublin Core metadata and extended EPUB metadata.
        Handles OPF format like:
        
        <dc:identifier opf:scheme="ISBN">9780375504877</dc:identifier>
        <dc:creator opf:file-as="Morris, Edmund" opf:role="aut">Edmund Morris</dc:creator>
        <meta name="calibre:series" content="Theodore Roosevelt"/>
        
        Args:
            book: ebooklib.epub.EpubBook object
            
        Returns:
            EpubMetadata with all available metadata fields
        """
        metadata = EpubMetadata()
        
        # Helper to safely get first metadata value
        def get_first(namespace: str, name: str) -> str:
            values = book.get_metadata(namespace, name)
            if values and values[0]:
                return str(values[0][0]) if values[0][0] else ""
            return ""
        
        # Helper to get all values for a metadata field
        def get_all(namespace: str, name: str) -> List[str]:
            values = book.get_metadata(namespace, name)
            return [str(v[0]) for v in values if v and v[0]]
        
        # Dublin Core (DC) metadata
        metadata.title = get_first('DC', 'title')
        metadata.publisher = get_first('DC', 'publisher')
        metadata.language = get_first('DC', 'language')
        metadata.description = get_first('DC', 'description')
        metadata.rights = get_first('DC', 'rights')
        metadata.date = get_first('DC', 'date')
        metadata.source = get_first('DC', 'source')
        metadata.relation = get_first('DC', 'relation')
        metadata.coverage = get_first('DC', 'coverage')
        metadata.type = get_first('DC', 'type')
        metadata.format = get_first('DC', 'format')
        
        # Get all subjects (categories/tags)
        metadata.subject = get_all('DC', 'subject')
        
        # Extract creator with full info (opf:file-as, opf:role)
        creators = book.get_metadata('DC', 'creator')
        if creators:
            for creator in creators:
                if creator and creator[0]:
                    name = str(creator[0])
                    attrs = creator[1] if len(creator) > 1 and creator[1] else {}
                    
                    # Get OPF attributes (with or without namespace prefix)
                    file_as = (attrs.get('{http://www.idpf.org/2007/opf}file-as') or 
                              attrs.get('opf:file-as') or 
                              attrs.get('file-as') or '')
                    role = (attrs.get('{http://www.idpf.org/2007/opf}role') or 
                           attrs.get('opf:role') or 
                           attrs.get('role') or '')
                    
                    # First creator is the primary author
                    if not metadata.creator:
                        metadata.creator = name
                        metadata.creator_info = CreatorInfo(
                            name=name,
                            file_as=file_as,
                            role=role
                        )
                    else:
                        # Additional creators as contributors
                        metadata.contributors.append(CreatorInfo(
                            name=name,
                            file_as=file_as,
                            role=role
                        ))
        
        # Extract contributors with full info
        contributors = book.get_metadata('DC', 'contributor')
        if contributors:
            for contrib in contributors:
                if contrib and contrib[0]:
                    name = str(contrib[0])
                    attrs = contrib[1] if len(contrib) > 1 and contrib[1] else {}
                    
                    file_as = (attrs.get('{http://www.idpf.org/2007/opf}file-as') or 
                              attrs.get('opf:file-as') or 
                              attrs.get('file-as') or '')
                    role = (attrs.get('{http://www.idpf.org/2007/opf}role') or 
                           attrs.get('opf:role') or 
                           attrs.get('role') or '')
                    
                    metadata.contributors.append(CreatorInfo(
                        name=name,
                        file_as=file_as,
                        role=role
                    ))
        
        # Get all identifiers with schemes (ISBN, UUID, calibre, GOOGLE, etc.)
        identifiers = book.get_metadata('DC', 'identifier')
        for ident in identifiers:
            if ident and ident[0]:
                value = str(ident[0])
                attrs = ident[1] if len(ident) > 1 and ident[1] else {}
                
                # Get scheme from various attribute formats
                scheme = (attrs.get('{http://www.idpf.org/2007/opf}scheme') or 
                         attrs.get('opf:scheme') or 
                         attrs.get('scheme') or '').upper()
                id_attr = attrs.get('id', '').lower()
                
                # Store in identifiers dict
                if scheme:
                    metadata.identifiers[scheme] = value
                
                # Also set specific fields based on scheme
                if scheme == 'ISBN' or 'isbn' in id_attr:
                    metadata.isbn = value
                elif scheme == 'UUID' or 'uuid' in id_attr:
                    metadata.uuid = value
                elif scheme == 'ASIN':
                    metadata.asin = value
                elif scheme == 'GOOGLE':
                    metadata.google_id = value
                elif scheme == 'CALIBRE' or 'calibre' in id_attr:
                    metadata.calibre_id = value
                elif not metadata.identifier:
                    metadata.identifier = value
        
        # Extract <meta> elements (calibre metadata, etc.)
        # Format: <meta name="calibre:series" content="Series Name"/>
        try:
            # Try different ways to get meta elements
            meta_items = book.get_metadata('OPF', 'meta') or []
            
            for meta in meta_items:
                if not meta:
                    continue
                    
                # meta format: (value, attrs) or just attrs dict
                attrs = {}
                if isinstance(meta, tuple):
                    attrs = meta[1] if len(meta) > 1 and meta[1] else {}
                elif isinstance(meta, dict):
                    attrs = meta
                
                name = attrs.get('name', '')
                content = attrs.get('content', '')
                
                if not name or not content:
                    continue
                
                # Handle specific calibre metadata
                name_lower = name.lower()
                if name_lower == 'calibre:series':
                    metadata.series = content
                elif name_lower == 'calibre:series_index':
                    metadata.series_index = content
                elif name_lower == 'calibre:timestamp':
                    metadata.calibre_timestamp = content
                elif name_lower == 'calibre:title_sort':
                    metadata.title_sort = content
                else:
                    # Store other meta as custom
                    metadata.custom[name] = content
                    
        except Exception as e:
            logger.debug("Could not extract OPF meta elements: %s", e)
        
        # Log extracted metadata
        logger.info("=" * 60)
        logger.info("EXTRACTED OPF METADATA")
        logger.info("=" * 60)
        logger.info("Title: %s", metadata.title)
        logger.info("Title Sort: %s", metadata.title_sort)
        logger.info("Author: %s", metadata.creator)
        if metadata.creator_info:
            logger.info("  File-as: %s, Role: %s", 
                       metadata.creator_info.file_as, metadata.creator_info.role)
        logger.info("Publisher: %s", metadata.publisher)
        logger.info("Language: %s", metadata.language)
        logger.info("Date: %s", metadata.date)
        logger.info("Identifiers:")
        for scheme, value in metadata.identifiers.items():
            logger.info("  %s: %s", scheme, value)
        logger.info("ISBN: %s", metadata.isbn)
        logger.info("UUID: %s", metadata.uuid)
        logger.info("Google ID: %s", metadata.google_id)
        logger.info("Calibre ID: %s", metadata.calibre_id)
        logger.info("Series: %s (#%s)", metadata.series, metadata.series_index)
        logger.info("Subjects: %s", metadata.subject)
        if metadata.contributors:
            logger.info("Contributors: %d", len(metadata.contributors))
            for c in metadata.contributors[:3]:
                logger.info("  %s (file-as: %s, role: %s)", c.name, c.file_as, c.role)
        if metadata.rights:
            logger.info("Rights: %s", metadata.rights[:50] + "..." if len(metadata.rights) > 50 else metadata.rights)
        if metadata.description:
            logger.info("Description: %s", metadata.description[:100] + "..." if len(metadata.description) > 100 else metadata.description)
        if metadata.custom:
            logger.info("Custom metadata: %d fields", len(metadata.custom))
            for key, value in list(metadata.custom.items())[:5]:
                logger.info("  %s: %s", key, str(value)[:50] if len(str(value)) > 50 else value)
        logger.info("=" * 60)
        
        return metadata

    def _extract_toc(self, book) -> List[TocEntry]:
        """
        Extract table of contents from EPUB's native navigation.
        
        This uses the same TOC structure that PDF converters use for bookmarks.
        Works with both EPUB2 (NCX) and EPUB3 (NAV) formats.
        
        Args:
            book: ebooklib.epub.EpubBook object
            
        Returns:
            List of TocEntry objects representing the hierarchical TOC
        """
        toc_entries = []
        
        def process_toc_item(item, level=0) -> Optional[TocEntry]:
            """Recursively process TOC items from ebooklib."""
            try:
                logger.debug("Processing TOC item type: %s, value: %s", type(item).__name__, str(item)[:100])
                
                if isinstance(item, tuple):
                    # Nested section: (Section/Link, [children])
                    if len(item) >= 2:
                        section_info, children = item[0], item[1]
                        
                        # Get title and href from section_info
                        title = ""
                        href = ""
                        
                        if hasattr(section_info, 'title'):
                            title = section_info.title or ""
                        if hasattr(section_info, 'href'):
                            href = section_info.href or ""
                        
                        logger.debug("  Tuple entry: title='%s', href='%s', children=%d",
                                   title[:30], href[:20], len(children) if children else 0)
                        
                        entry = TocEntry(title=title, href=href)
                        
                        # Process children - can be list or tuple
                        if children:
                            if isinstance(children, (list, tuple)):
                                for child in children:
                                    child_entry = process_toc_item(child, level + 1)
                                    if child_entry:
                                        entry.children.append(child_entry)
                        
                        return entry if title else None
                    elif len(item) == 1:
                        # Single item in tuple
                        return process_toc_item(item[0], level)
                        
                elif hasattr(item, 'title'):
                    # Single item (epub.Link or epub.Section)
                    title = item.title or ""
                    href = getattr(item, 'href', "") or ""
                    logger.debug("  Link/Section: title='%s', href='%s'", title[:30], href[:20])
                    return TocEntry(title=title, href=href) if title else None
                
                elif isinstance(item, list):
                    # Sometimes children are passed as a list directly
                    logger.debug("  List with %d items", len(item))
                    results = []
                    for subitem in item:
                        subentry = process_toc_item(subitem, level)
                        if subentry:
                            results.append(subentry)
                    # If we got results, return them as children of a placeholder
                    if results:
                        if len(results) == 1:
                            return results[0]
                        # Multiple results - return first with others as siblings
                        # This shouldn't normally happen
                        return results[0]
                    
            except Exception as e:
                logger.warning("Error processing TOC item: %s - %s", type(item).__name__, e)
            
            return None
        
        # Method 1: Get TOC from ebooklib's parsed structure
        if hasattr(book, 'toc') and book.toc:
            logger.info("Found ebooklib TOC with %d top-level items", len(book.toc))
            logger.info("TOC item types: %s", [type(item).__name__ for item in book.toc[:10]])
            
            for item in book.toc:
                entry = process_toc_item(item)
                if entry and entry.title:
                    toc_entries.append(entry)
            
            logger.info("Extracted %d entries from ebooklib TOC", len(toc_entries))
        
        # Method 2: If ebooklib TOC is empty/flat, try parsing NCX directly
        if not toc_entries or all(not e.children for e in toc_entries):
            ncx_entries = self._parse_ncx_directly(book)
            if ncx_entries and len(ncx_entries) > len(toc_entries):
                logger.info("Using NCX-parsed TOC (%d entries vs %d from ebooklib)",
                           len(ncx_entries), len(toc_entries))
                toc_entries = ncx_entries
        
        # Method 3: Try parsing NAV document for EPUB3
        if not toc_entries or all(not e.children for e in toc_entries):
            nav_entries = self._parse_nav_directly(book)
            if nav_entries and len(nav_entries) > len(toc_entries):
                logger.info("Using NAV-parsed TOC (%d entries vs %d)",
                           len(nav_entries), len(toc_entries))
                toc_entries = nav_entries
        
        # Log TOC structure with full detail
        def log_toc(entries, indent=0, prefix=""):
            for i, entry in enumerate(entries):
                child_count = self._count_all_children(entry)
                num = f"{prefix}{i+1}" if prefix else f"{i+1}"
                logger.info("%s%s. %s [%s] (%d children)",
                           "  " * indent,
                           num,
                           entry.title[:50] if entry.title else "(no title)",
                           entry.href[:30] if entry.href else "",
                           child_count)
                if entry.children:
                    log_toc(entry.children, indent + 1, f"{num}.")
        
        total_entries = sum(1 + self._count_all_children(e) for e in toc_entries)
        
        # Count entries at each level
        def count_at_level(entries, level=1):
            counts = {level: len(entries)}
            for entry in entries:
                if entry.children:
                    child_counts = count_at_level(entry.children, level + 1)
                    for lvl, cnt in child_counts.items():
                        counts[lvl] = counts.get(lvl, 0) + cnt
            return counts
        
        level_counts = count_at_level(toc_entries)
        level_str = ", ".join(f"L{k}:{v}" for k, v in sorted(level_counts.items()))
        
        logger.info("=" * 60)
        logger.info("TOC EXTRACTION SUMMARY")
        logger.info("=" * 60)
        logger.info("Total entries: %d (by level: %s)", total_entries, level_str)
        logger.info("-" * 60)
        
        if toc_entries:
            log_toc(toc_entries)
        else:
            logger.warning("No TOC entries found!")
        
        logger.info("=" * 60)
        
        return toc_entries

    def _count_all_children(self, entry: TocEntry) -> int:
        """Count all descendants of a TOC entry."""
        count = len(entry.children)
        for child in entry.children:
            count += self._count_all_children(child)
        return count

    def _parse_ncx_directly(self, book) -> List[TocEntry]:
        """
        Parse NCX file directly for EPUB2 table of contents.
        
        NCX (Navigation Control file for XML) contains the full hierarchical TOC.
        """
        entries = []
        
        try:
            # Find NCX item
            ncx_item = None
            for item in book.get_items():
                name = item.get_name().lower()
                media_type = getattr(item, 'media_type', '') or ''
                if name.endswith('.ncx') or 'ncx' in media_type.lower():
                    ncx_item = item
                    break
            
            if not ncx_item:
                return entries
            
            ncx_content = ncx_item.get_content().decode('utf-8', errors='ignore')
            logger.info("Found NCX file (%d bytes), parsing directly", len(ncx_content))
            
            # Try XML parsing first
            try:
                import xml.etree.ElementTree as ET
                # Remove XML namespace to simplify parsing
                ncx_clean = re.sub(r'\s+xmlns[^"]*"[^"]*"', '', ncx_content)
                ncx_clean = re.sub(r'<\?xml[^?]*\?>', '', ncx_clean)
                root = ET.fromstring(ncx_clean)
                
                def parse_navpoint_element(elem) -> Optional[TocEntry]:
                    """Parse a navPoint XML element."""
                    # Get title from navLabel/text
                    title = ""
                    nav_label = elem.find('.//navLabel')
                    if nav_label is not None:
                        text_elem = nav_label.find('text')
                        if text_elem is not None and text_elem.text:
                            title = text_elem.text.strip()
                    
                    # Get href from content[@src]
                    href = ""
                    content_elem = elem.find('content')
                    if content_elem is not None:
                        href = content_elem.get('src', '')
                    
                    if not title:
                        return None
                    
                    entry = TocEntry(title=title, href=href)
                    
                    # Parse child navPoints
                    for child_elem in elem.findall('navPoint'):
                        child_entry = parse_navpoint_element(child_elem)
                        if child_entry:
                            entry.children.append(child_entry)
                    
                    return entry
                
                # Find navMap and parse navPoints
                nav_map = root.find('.//navMap')
                if nav_map is not None:
                    for nav_point in nav_map.findall('navPoint'):
                        entry = parse_navpoint_element(nav_point)
                        if entry:
                            entries.append(entry)
                    logger.info("XML-parsed NCX: found %d top-level entries", len(entries))
                    return entries
                    
            except Exception as xml_err:
                logger.debug("XML parsing failed, using regex: %s", xml_err)
            
            # Fallback to regex parsing
            def parse_nav_points_regex(content: str) -> List[TocEntry]:
                """Parse navPoint elements using regex."""
                results = []
                
                # Find top-level navPoints only (not nested ones)
                depth = 0
                current_start = -1
                i = 0
                
                while i < len(content):
                    lower_chunk = content[i:i+15].lower()
                    
                    if lower_chunk.startswith('<navpoint'):
                        if depth == 0:
                            current_start = i
                        depth += 1
                        i += 9
                    elif lower_chunk.startswith('</navpoint>'):
                        depth -= 1
                        if depth == 0 and current_start >= 0:
                            nav_point_content = content[current_start:i+11]
                            entry = parse_single_nav_point_regex(nav_point_content)
                            if entry:
                                results.append(entry)
                            current_start = -1
                        i += 11
                    else:
                        i += 1
                
                return results
            
            def parse_single_nav_point_regex(nav_point_xml: str) -> Optional[TocEntry]:
                """Parse a single navPoint element using regex."""
                # Extract title
                label_match = re.search(
                    r'<navLabel[^>]*>\s*<text[^>]*>(.*?)</text>\s*</navLabel>',
                    nav_point_xml, re.IGNORECASE | re.DOTALL
                )
                title = self._extract_text(label_match.group(1)).strip() if label_match else ""
                
                # Extract href
                content_match = re.search(
                    r'<content[^>]*src=["\']([^"\']+)["\']',
                    nav_point_xml, re.IGNORECASE
                )
                href = content_match.group(1) if content_match else ""
                
                if not title:
                    return None
                
                entry = TocEntry(title=title, href=href)
                
                # Extract inner content (after first navLabel and content)
                # Find position after first </content> or <content/>
                first_content_end = re.search(
                    r'<content[^>]*/?>|</content>',
                    nav_point_xml, re.IGNORECASE
                )
                if first_content_end:
                    inner_start = first_content_end.end()
                    # Find the closing </navPoint> for this element
                    inner_end = nav_point_xml.rfind('</navPoint>')
                    if inner_end > inner_start:
                        inner_content = nav_point_xml[inner_start:inner_end]
                        # Parse nested navPoints
                        entry.children = parse_nav_points_regex(inner_content)
                
                return entry
            
            # Find navMap and parse
            nav_map_match = re.search(
                r'<navMap[^>]*>(.*)</navMap>',
                ncx_content, re.IGNORECASE | re.DOTALL
            )
            if nav_map_match:
                entries = parse_nav_points_regex(nav_map_match.group(1))
                logger.info("Regex-parsed NCX: found %d top-level entries", len(entries))
            
        except Exception as e:
            logger.warning("Failed to parse NCX directly: %s", e)
        
        return entries

    def _parse_nav_directly(self, book) -> List[TocEntry]:
        """
        Parse NAV document directly for EPUB3 table of contents.
        
        EPUB3 uses an XHTML navigation document with nested <ol>/<li> lists.
        """
        entries = []
        
        try:
            # Find NAV item
            nav_item = None
            for item in book.get_items():
                # Check for nav property or common nav filenames
                props = getattr(item, 'properties', []) or []
                if 'nav' in props:
                    nav_item = item
                    break
                name = item.get_name().lower()
                if 'nav' in name and name.endswith(('.xhtml', '.html', '.xml')):
                    nav_item = item
                    break
                # Also check for toc in filename
                if 'toc' in name and name.endswith(('.xhtml', '.html', '.xml')):
                    nav_item = item
                    # Don't break, prefer 'nav' files
            
            if not nav_item:
                return entries
            
            nav_content = nav_item.get_content().decode('utf-8', errors='ignore')
            logger.info("Found NAV document (%d bytes), parsing directly", len(nav_content))
            
            # Try XML parsing first
            try:
                import xml.etree.ElementTree as ET
                # Clean up for XML parsing
                nav_clean = re.sub(r'\s+xmlns[^"]*"[^"]*"', '', nav_content)
                nav_clean = re.sub(r'epub:', '', nav_clean)  # Remove epub namespace prefix
                nav_clean = re.sub(r'<\?xml[^?]*\?>', '', nav_clean)
                nav_clean = re.sub(r'<!DOCTYPE[^>]*>', '', nav_clean)
                
                root = ET.fromstring(nav_clean)
                
                def parse_ol_element(ol_elem) -> List[TocEntry]:
                    """Parse an <ol> element recursively."""
                    results = []
                    for li in ol_elem.findall('li'):
                        # Find the <a> link
                        a_elem = li.find('a')
                        if a_elem is not None:
                            href = a_elem.get('href', '')
                            # Get text content (handle nested elements)
                            title = ''.join(a_elem.itertext()).strip()
                            
                            if title:
                                entry = TocEntry(title=title, href=href)
                                
                                # Find nested <ol>
                                nested_ol = li.find('ol')
                                if nested_ol is not None:
                                    entry.children = parse_ol_element(nested_ol)
                                
                                results.append(entry)
                        else:
                            # Sometimes <span> is used instead of <a>
                            span_elem = li.find('span')
                            if span_elem is not None:
                                title = ''.join(span_elem.itertext()).strip()
                                if title:
                                    entry = TocEntry(title=title, href='')
                                    nested_ol = li.find('ol')
                                    if nested_ol is not None:
                                        entry.children = parse_ol_element(nested_ol)
                                    results.append(entry)
                    return results
                
                # Find nav element with type="toc" or just nav
                for nav in root.iter('nav'):
                    nav_type = nav.get('type', '')
                    if 'toc' in nav_type.lower() or not nav_type:
                        ol = nav.find('ol')
                        if ol is not None:
                            entries = parse_ol_element(ol)
                            if entries:
                                logger.info("XML-parsed NAV: found %d top-level entries", len(entries))
                                return entries
                
                # If no nav found, try finding ol directly
                for ol in root.iter('ol'):
                    entries = parse_ol_element(ol)
                    if entries:
                        logger.info("XML-parsed NAV (ol): found %d entries", len(entries))
                        return entries
                        
            except Exception as xml_err:
                logger.debug("XML parsing failed for NAV, using regex: %s", xml_err)
            
            # Fallback to regex parsing
            # Find the toc nav element
            toc_nav_match = re.search(
                r'<nav[^>]*(?:epub:)?type=["\']toc["\'][^>]*>(.*?)</nav>',
                nav_content, re.IGNORECASE | re.DOTALL
            )
            if not toc_nav_match:
                # Try any nav
                toc_nav_match = re.search(
                    r'<nav[^>]*>(.*?)</nav>',
                    nav_content, re.IGNORECASE | re.DOTALL
                )
            
            if not toc_nav_match:
                return entries
            
            nav_html = toc_nav_match.group(1)
            
            def parse_nav_list_regex(html: str) -> List[TocEntry]:
                """Parse <ol>/<li> navigation list using regex."""
                results = []
                
                # Find top-level <li> elements
                depth = 0
                current_start = -1
                i = 0
                li_contents = []
                
                while i < len(html):
                    chunk = html[i:i+5].lower()
                    if chunk.startswith('<li'):
                        if depth == 0:
                            current_start = i
                        depth += 1
                        i += 3
                    elif chunk.startswith('</li>'):
                        depth -= 1
                        if depth == 0 and current_start >= 0:
                            li_contents.append(html[current_start:i+5])
                            current_start = -1
                        i += 5
                    else:
                        i += 1
                
                for li_html in li_contents:
                    # Extract <a> link - be flexible with attribute order
                    a_match = re.search(
                        r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                        li_html, re.IGNORECASE | re.DOTALL
                    )
                    if not a_match:
                        # Try href after other attributes
                        a_match = re.search(
                            r'<a[^>]*>(.*?)</a>',
                            li_html, re.IGNORECASE | re.DOTALL
                        )
                        if a_match:
                            href_match = re.search(r'href=["\']([^"\']+)["\']', li_html[:a_match.start(1)])
                            if href_match:
                                href = href_match.group(1)
                                title = self._extract_text(a_match.group(1)).strip()
                            else:
                                continue
                        else:
                            continue
                    else:
                        href = a_match.group(1)
                        title = self._extract_text(a_match.group(2)).strip()
                    
                    if title:
                        entry = TocEntry(title=title, href=href)
                        
                        # Look for nested <ol> - need to handle nested properly
                        # Find <ol> that's inside this <li> but after the </a>
                        a_end = li_html.find('</a>')
                        if a_end > 0:
                            after_a = li_html[a_end:]
                            nested_ol_match = re.search(
                                r'<ol[^>]*>(.*)</ol>',
                                after_a, re.IGNORECASE | re.DOTALL
                            )
                            if nested_ol_match:
                                entry.children = parse_nav_list_regex(nested_ol_match.group(1))
                        
                        results.append(entry)
                
                return results
            
            # Find main <ol> and parse it
            ol_match = re.search(r'<ol[^>]*>(.*)</ol>', nav_html, re.IGNORECASE | re.DOTALL)
            if ol_match:
                entries = parse_nav_list_regex(ol_match.group(1))
                logger.info("Regex-parsed NAV: found %d entries", len(entries))
            
        except Exception as e:
            logger.warning("Failed to parse NAV directly: %s", e)
        
        return entries

    def _get_content_for_toc_entry(self, book, href: str, items_map: Dict[str, Any]) -> tuple:
        """
        Get content for a TOC entry by its href.
        
        Args:
            book: EPUB book object
            href: TOC entry href (e.g., "chapter1.xhtml" or "chapter1.xhtml#section2")
            items_map: Map of file names to EPUB items
            
        Returns:
            Tuple of (text_content, html_content, word_count)
        """
        if not href:
            return "", "", 0
        
        # Split href into filename and fragment
        if '#' in href:
            filename, fragment = href.split('#', 1)
        else:
            filename = href
            fragment = None
        
        # Find the item - try multiple variations
        item = items_map.get(filename)
        if not item:
            # Try without leading path
            base_filename = filename.split('/')[-1] if '/' in filename else filename
            item = items_map.get(base_filename)
        if not item:
            # Try with path variations
            for key in items_map:
                key_base = key.split('/')[-1] if '/' in key else key
                if key_base == filename or key.endswith(filename) or filename.endswith(key):
                    item = items_map[key]
                    break
        
        if not item:
            logger.debug("Content not found for href: %s", href)
            return "", "", 0
        
        try:
            full_html = item.get_content().decode('utf-8', errors='ignore')
            html_content = full_html
            
            # If there's a fragment, extract just that section
            if fragment:
                # Method 1: Find element with this ID and extract until next same-level heading
                # Look for the element with id="fragment"
                id_patterns = [
                    rf'<[^>]+id=["\']?{re.escape(fragment)}["\']?[^>]*>',
                    rf'<[^>]+name=["\']?{re.escape(fragment)}["\']?[^>]*>',
                ]
                
                start_pos = None
                for pattern in id_patterns:
                    match = re.search(pattern, full_html, re.IGNORECASE)
                    if match:
                        start_pos = match.start()
                        break
                
                if start_pos is not None:
                    # Find the next heading at same or higher level, or end of content
                    remaining = full_html[start_pos:]
                    
                    # Find what heading level we're in
                    heading_match = re.search(r'<(h[1-6])[^>]*>', remaining[:200], re.IGNORECASE)
                    if heading_match:
                        current_level = int(heading_match.group(1)[1])
                        # Find next heading at same or higher level
                        next_heading_pattern = rf'<h[1-{current_level}][^>]*>'
                        next_match = re.search(next_heading_pattern, remaining[1:], re.IGNORECASE)
                        if next_match:
                            html_content = remaining[:next_match.start() + 1]
                        else:
                            html_content = remaining
                    else:
                        # No heading, take until next any heading or reasonable chunk
                        next_h = re.search(r'<h[1-6][^>]*>', remaining[100:], re.IGNORECASE)
                        if next_h:
                            html_content = remaining[:next_h.start() + 100]
                        else:
                            html_content = remaining
            
            text_content = self._extract_text(html_content)
            word_count = len(text_content.split())
            
            return text_content, html_content, word_count
        except Exception as e:
            logger.warning("Failed to get content for %s: %s", href, e)
            return "", "", 0

    def _extract_title_from_html(self, html_content: str) -> Optional[str]:
        """Try to extract title from HTML headings or title tag."""
        # Try h1, h2, h3 tags
        for tag in ['h1', 'h2', 'h3', 'title']:
            match = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', html_content, re.IGNORECASE | re.DOTALL)
            if match:
                title = self._extract_text(match.group(1)).strip()
                if title and len(title) < 200:  # Reasonable title length
                    return title
        return None

    def _extract_subsections(self, html_content: str, parent_order: int,
                             start_order: int) -> List[Section]:
        """
        Extract sub-sections from HTML content based on heading tags (h2, h3, h4, h5, h6).
        
        Args:
            html_content: HTML content to parse for sub-sections
            parent_order: Order of the parent section
            start_order: Starting order number for sub-sections
            
        Returns:
            List of Section objects representing sub-sections
        """
        subsections = []
        
        # Find all headings h2-h6 that could indicate sub-sections
        heading_pattern = r'<(h[2-6])[^>]*>(.*?)</\1>'
        headings = list(re.finditer(heading_pattern, html_content, re.IGNORECASE | re.DOTALL))
        
        if not headings:
            return subsections
        
        order = start_order
        
        for i, match in enumerate(headings):
            tag = match.group(1).lower()
            heading_text = self._extract_text(match.group(2)).strip()
            
            if not heading_text or len(heading_text) > 200:
                continue
            
            # Determine the content for this subsection
            start_pos = match.end()
            if i + 1 < len(headings):
                end_pos = headings[i + 1].start()
            else:
                end_pos = len(html_content)
            
            subsection_html = html_content[start_pos:end_pos]
            subsection_text = self._extract_text(subsection_html).strip()
            
            # Skip very short subsections
            word_count = len(subsection_text.split())
            if word_count < 5:
                continue
            
            # Determine level from heading tag
            level = int(tag[1])  # h2 -> 2, h3 -> 3, etc.
            
            subsection = Section(
                type='subsection',
                title=heading_text,
                content=subsection_text,
                html_content=subsection_html,
                order=order,
                word_count=word_count,
                level=level,
                parent_order=parent_order
            )
            subsections.append(subsection)
            order += 1
        
        return subsections

    def _split_by_headings(self, html_content: str, filename: str) -> List[Dict[str, Any]]:
        """
        Split HTML content into multiple sections based on h1 headings.
        
        Many EPUBs have multiple chapters in a single HTML file, each starting with h1.
        This method splits them into separate sections.
        
        Args:
            html_content: HTML content that may contain multiple h1 sections
            filename: Original filename for fallback titles
            
        Returns:
            List of section dictionaries
        """
        sections = []
        matches = []
        heading_level = 'h1'
        
        # Try h1 first, then h2 if h1 doesn't give multiple sections
        for level in ['h1', 'h2']:
            pattern = rf'<{level}[^>]*>(.*?)</{level}>'
            found = list(re.finditer(pattern, html_content, re.IGNORECASE | re.DOTALL))
            if len(found) >= 2:
                matches = found
                heading_level = level
                break
        
        # If no headings give multiple sections, return empty
        if len(matches) < 2:
            return []
        
        logger.info("Found %d %s headings in %s, splitting", len(matches), heading_level.upper(), filename)
        
        for i, match in enumerate(matches):
            heading_text = self._extract_text(match.group(1)).strip()
            
            if not heading_text:
                heading_text = f"Section {i + 1}"
            
            # Get content from this heading to the next (or end)
            start_pos = match.start()
            if i + 1 < len(matches):
                end_pos = matches[i + 1].start()
            else:
                end_pos = len(html_content)
            
            section_html = html_content[start_pos:end_pos]
            section_text = self._extract_text(section_html).strip()
            word_count = len(section_text.split())
            
            # Skip very short sections
            if word_count < 10:
                continue
            
            # Classify based on title
            section_type = self._classify_section(heading_text, filename)
            if section_type == 'skip':
                continue
            
            sections.append({
                'title': heading_text,
                'content': section_text,
                'html_content': section_html,
                'word_count': word_count,
                'type': section_type
            })
        
        return sections

    def _classify_section(self, title: str, filename: str) -> str:
        """
        Classify a section based on its title and filename.
        
        Returns: Section type string.
        """
        # Combine title and filename for pattern matching
        check_strings = [
            title.lower().strip() if title else '',
            filename.lower().strip() if filename else ''
        ]
        
        for check_str in check_strings:
            if not check_str:
                continue
                
            # Check skip patterns first
            for pattern in self.SKIP_PATTERNS:
                if re.search(pattern, check_str, re.IGNORECASE):
                    return 'skip'
            
            # Check prologue
            for pattern in self.PROLOGUE_PATTERNS:
                if re.search(pattern, check_str, re.IGNORECASE):
                    return 'prologue'
            
            # Check epilogue
            for pattern in self.EPILOGUE_PATTERNS:
                if re.search(pattern, check_str, re.IGNORECASE):
                    return 'epilogue'
            
            # Check foreword
            for pattern in self.FOREWORD_PATTERNS:
                if re.search(pattern, check_str, re.IGNORECASE):
                    return 'foreword'
            
            # Check afterword
            for pattern in self.AFTERWORD_PATTERNS:
                if re.search(pattern, check_str, re.IGNORECASE):
                    return 'afterword'
            
            # Check introduction
            for pattern in self.INTRODUCTION_PATTERNS:
                if re.search(pattern, check_str, re.IGNORECASE):
                    return 'introduction'
            
            # Check chapter
            for pattern in self.CHAPTER_PATTERNS:
                if re.search(pattern, check_str, re.IGNORECASE):
                    return 'chapter'
        
        return 'unknown'
    
    def _get_chapter_number(self, title: str) -> Optional[int]:
        """Extract chapter number from title."""
        # Try numeric patterns
        match = re.search(r'(\d+)', title)
        if match:
            return int(match.group(1))
        
        # Try Roman numerals
        roman_match = re.search(r'\b([IVXLCDM]+)\b', title, re.IGNORECASE)
        if roman_match:
            return self._roman_to_int(roman_match.group(1))
        
        return None
    
    @staticmethod
    def _roman_to_int(roman: str) -> int:
        """Convert Roman numeral to integer."""
        values = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
        roman = roman.upper()
        result = 0
        prev = 0
        for char in reversed(roman):
            curr = values.get(char, 0)
            if curr < prev:
                result -= curr
            else:
                result += curr
            prev = curr
        return result

    def _parse_from_toc(self, book, structure: BookStructure,
                        toc_entries: List[TocEntry],
                        items_map: Dict[str, Any]) -> BookStructure:
        """
        Parse EPUB using the table of contents structure.
        
        This extracts just the TOC structure (bookmarks) without content.
        Content is extracted later only for selected sections.
        
        Args:
            book: EPUB book object
            structure: BookStructure to populate
            toc_entries: List of TocEntry from the TOC
            items_map: Map of filenames to EPUB items
            
        Returns:
            Populated BookStructure with section structure (no content yet)
        """
        order = 0
        skipped_entries = []
        
        def process_toc_entry(entry: TocEntry, level: int = 1,
                              parent_order: Optional[int] = None) -> Optional[Section]:
            """Process a single TOC entry and its children - structure only, no content."""
            nonlocal order
            
            # Classify the section type
            section_type = self._classify_section(entry.title, entry.href or "")
            
            # Log what we're processing
            logger.info("Processing TOC entry: '%s' [%s] -> type: %s",
                       entry.title[:50] if entry.title else "(empty)",
                       entry.href[:30] if entry.href else "",
                       section_type)
            
            # Skip items marked for skipping (but still process children!)
            if section_type == 'skip':
                skipped_entries.append(entry.title)
                # Still process children - they might be important!
                for child_entry in entry.children:
                    child_section = process_toc_entry(child_entry, level, parent_order)
                    if child_section:
                        structure.chapters.append(child_section)
                return None
            
            # Create section - NO CONTENT YET, just structure
            current_order = order
            order += 1
            
            section = Section(
                type=section_type if section_type != 'unknown' else 'chapter',
                title=entry.title,
                content="",  # Content extracted later for selected sections
                html_content=None,
                order=current_order,
                word_count=0,
                level=level,
                parent_order=parent_order,
                href=entry.href or ""  # Store href for later content extraction
            )
            
            # Process children recursively
            if entry.children:
                logger.info("Entry '%s' has %d children to process",
                           entry.title[:30], len(entry.children))
            for child_entry in entry.children:
                child_section = process_toc_entry(child_entry, level + 1, current_order)
                if child_section:
                    section.children.append(child_section)
                    logger.info("  -> Added child '%s' to '%s' (now has %d children)",
                               child_section.title[:30], section.title[:30],
                               len(section.children))
            
            return section
        
        # Process all top-level TOC entries
        logger.info("=" * 60)
        logger.info("PROCESSING %d TOP-LEVEL TOC ENTRIES", len(toc_entries))
        logger.info("=" * 60)
        
        for entry in toc_entries:
            section = process_toc_entry(entry)
            if section is not None:
                structure.chapters.append(section)
        
        # Count total sections
        def count_sections(sections):
            count = len(sections)
            for s in sections:
                count += count_sections(s.children)
            return count
        
        total_sections = count_sections(structure.chapters)
        
        logger.info("=" * 60)
        logger.info("TOC PARSING COMPLETE")
        logger.info("  Top-level sections: %d", len(structure.chapters))
        logger.info("  Total sections (including nested): %d", total_sections)
        logger.info("  Skipped entries: %d (%s)", len(skipped_entries), 
                   ", ".join(skipped_entries[:5]) + ("..." if len(skipped_entries) > 5 else ""))
        logger.info("=" * 60)
        
        # List all sections for debugging
        def log_sections(sections, indent=0):
            for s in sections:
                logger.info("%s[%d] %s (%s) href=%s",
                           "  " * indent, s.order, s.title[:40], s.type, s.href[:30] if s.href else "")
                if s.children:
                    log_sections(s.children, indent + 1)
        
        logger.info("FINAL SECTION STRUCTURE:")
        log_sections(structure.chapters)
        
        return structure

    def _parse_external_opf(self, opf_path: Union[str, Path]) -> Optional[EpubMetadata]:
        """
        Parse an external OPF file to extract metadata.
        
        Args:
            opf_path: Path to the OPF file
            
        Returns:
            EpubMetadata extracted from the OPF file
        """
        try:
            import xml.etree.ElementTree as ET
            
            opf_path = Path(opf_path)
            if not opf_path.exists():
                logger.warning("External OPF file not found: %s", opf_path)
                return None
            
            logger.info("Parsing external OPF file: %s", opf_path)
            
            with open(opf_path, 'r', encoding='utf-8') as f:
                opf_content = f.read()
            
            logger.info("OPF file size: %d chars", len(opf_content))
            
            # Define namespaces used in OPF files
            namespaces = {
                'opf': 'http://www.idpf.org/2007/opf',
                'dc': 'http://purl.org/dc/elements/1.1/',
                'dcterms': 'http://purl.org/dc/terms/',
                'calibre': 'http://calibre.kovidgoyal.net/2009/metadata',
            }
            
            # Register namespaces to preserve prefixes in output
            for prefix, uri in namespaces.items():
                ET.register_namespace(prefix, uri)
            
            root = ET.fromstring(opf_content)
            metadata = EpubMetadata()
            logger.info("XML root tag: %s", root.tag)
            
            # Helper to get attribute with various prefixes (opf:, dc:, or no prefix)
            def get_attr(attrs: dict, name: str) -> str:
                """Get attribute value, checking with opf: prefix and without."""
                return (attrs.get(f'{{http://www.idpf.org/2007/opf}}{name}') or
                        attrs.get(f'opf:{name}') or 
                        attrs.get(name) or 
                        '')
            
            # Find metadata element with various namespace possibilities
            meta_elem = root.find('.//{http://www.idpf.org/2007/opf}metadata')
            if meta_elem is None:
                meta_elem = root.find('.//metadata')
            if meta_elem is None:
                # Look for any element with 'metadata' in tag
                for elem in root.iter():
                    if 'metadata' in elem.tag.lower():
                        meta_elem = elem
                        break
            if meta_elem is None:
                meta_elem = root
            
            logger.info("Found metadata element: %s with %d children", meta_elem.tag, len(list(meta_elem)))
            
            # Extract Dublin Core metadata
            for elem in meta_elem:
                # Get tag name without namespace URI
                tag = elem.tag
                if '}' in tag:
                    tag = tag.split('}')[-1]
                # Remove dc: prefix if present (for non-namespaced XML)
                if ':' in tag:
                    tag = tag.split(':')[-1]
                tag = tag.lower()
                
                text = elem.text.strip() if elem.text else ""
                attrs = elem.attrib
                
                logger.info("Element: tag=%s, text='%s', attrs=%s", tag, text[:50] if text else "", dict(attrs))
                
                if tag == 'title':
                    metadata.title = text
                elif tag == 'creator':
                    file_as = get_attr(attrs, 'file-as')
                    role = get_attr(attrs, 'role')
                    
                    if not metadata.creator:
                        metadata.creator = text
                        metadata.creator_info = CreatorInfo(name=text, file_as=file_as, role=role)
                        logger.info("Creator: %s (file-as: %s, role: %s)", text, file_as, role)
                    else:
                        metadata.contributors.append(CreatorInfo(
                            name=text, file_as=file_as, role=role
                        ))
                elif tag == 'contributor':
                    file_as = get_attr(attrs, 'file-as')
                    role = get_attr(attrs, 'role')
                    metadata.contributors.append(CreatorInfo(
                        name=text, file_as=file_as, role=role
                    ))
                    logger.info("Contributor: %s (file-as: %s, role: %s)", text, file_as, role)
                elif tag == 'publisher':
                    metadata.publisher = text
                elif tag == 'language':
                    metadata.language = text
                elif tag == 'description':
                    metadata.description = text
                elif tag == 'subject':
                    if text:
                        metadata.subject.append(text)
                elif tag == 'rights':
                    metadata.rights = text
                elif tag == 'date':
                    metadata.date = text
                elif tag == 'identifier':
                    # Get scheme from opf:scheme or scheme attribute
                    scheme = get_attr(attrs, 'scheme').upper()
                    id_attr = attrs.get('id', '').lower()
                    
                    logger.info("Identifier: scheme=%s, id=%s, value=%s", scheme, id_attr, text)
                    
                    # Store in identifiers dict
                    if scheme:
                        metadata.identifiers[scheme] = text
                    
                    # Set specific identifier fields
                    if scheme == 'ISBN' or 'isbn' in id_attr:
                        metadata.isbn = text
                    elif scheme == 'UUID' or 'uuid' in id_attr:
                        metadata.uuid = text
                    elif scheme == 'ASIN':
                        metadata.asin = text
                    elif scheme == 'GOOGLE':
                        metadata.google_id = text
                    elif scheme == 'CALIBRE' or 'calibre' in id_attr:
                        metadata.calibre_id = text
                    
                    # Set primary identifier if not set
                    if not metadata.identifier:
                        metadata.identifier = text
                        
                elif tag == 'meta':
                    name = attrs.get('name', '')
                    content = attrs.get('content', '')
                    if name and content:
                        name_lower = name.lower()
                        logger.info("Meta: %s = %s", name, content[:50] if content else "")
                        if name_lower == 'calibre:series':
                            metadata.series = content
                        elif name_lower == 'calibre:series_index':
                            metadata.series_index = content
                        elif name_lower == 'calibre:timestamp':
                            metadata.calibre_timestamp = content
                        elif name_lower == 'calibre:title_sort':
                            metadata.title_sort = content
                        else:
                            metadata.custom[name] = content
            
            logger.info("=" * 60)
            logger.info("EXTRACTED EXTERNAL OPF METADATA")
            logger.info("=" * 60)
            logger.info("Title: %s", metadata.title)
            logger.info("Title Sort: %s", metadata.title_sort)
            logger.info("Author: %s", metadata.creator)
            if metadata.creator_info:
                logger.info("  File-as: %s, Role: %s", 
                           metadata.creator_info.file_as, metadata.creator_info.role)
            logger.info("Publisher: %s", metadata.publisher)
            logger.info("Date: %s", metadata.date)
            logger.info("ISBN: %s", metadata.isbn)
            logger.info("UUID: %s", metadata.uuid)
            logger.info("Google ID: %s", metadata.google_id)
            logger.info("Calibre ID: %s", metadata.calibre_id)
            logger.info("Series: %s (#%s)", metadata.series, metadata.series_index)
            logger.info("Subjects: %s", metadata.subject)
            logger.info("Identifiers: %s", metadata.identifiers)
            logger.info("=" * 60)
            
            return metadata
            
        except Exception as e:
            logger.exception("Failed to parse external OPF: %s", e)
            return None

    def parse(self, epub_source: Union[str, Path, BinaryIO],
              external_opf_path: Optional[Union[str, Path]] = None) -> BookStructure:
        """
        Parse an EPUB file and extract its structure.
        
        Args:
            epub_source: Path to EPUB file or file-like object.
            external_opf_path: Optional path to external OPF metadata file.
            
        Returns:
            BookStructure containing parsed content.
        """
        logger.info("Starting EPUB parsing")
        
        # Read EPUB file
        if isinstance(epub_source, (str, Path)):
            epub_path = Path(epub_source)
            if not epub_path.exists():
                raise FileNotFoundError(f"EPUB file not found: {epub_path}")
            logger.info("Reading EPUB from: %s", epub_path)
            book = epub.read_epub(str(epub_path))
        else:
            logger.info("Reading EPUB from stream")
            with tempfile.NamedTemporaryFile(suffix='.epub', delete=False) as tmp:
                tmp.write(epub_source.read())
                tmp_path = tmp.name
            try:
                book = epub.read_epub(tmp_path)
            finally:
                os.unlink(tmp_path)
        
        # Extract metadata - prefer external OPF if provided
        epub_metadata = None
        if external_opf_path:
            logger.info("=" * 60)
            logger.info("EXTERNAL OPF PATH PROVIDED: %s", external_opf_path)
            logger.info("=" * 60)
            epub_metadata = self._parse_external_opf(external_opf_path)
            if epub_metadata:
                logger.info("Successfully parsed external OPF!")
                logger.info("  Title: %s", epub_metadata.title)
                logger.info("  ISBN: %s", epub_metadata.isbn)
                logger.info("  UUID: %s", epub_metadata.uuid)
            else:
                logger.warning("External OPF parsing returned None!")
        
        # Fall back to embedded metadata if external parsing failed
        if epub_metadata is None:
            logger.info("Using embedded EPUB metadata")
            epub_metadata = self._extract_opf_metadata(book)
        
        logger.info("Parsing: '%s' by %s", epub_metadata.title, epub_metadata.creator)
        
        # Initialize structure with full metadata
        structure = BookStructure(
            title=epub_metadata.title or 'Untitled',
            author=epub_metadata.creator or 'Unknown Author',
            language=epub_metadata.language,
            publisher=epub_metadata.publisher,
            description=epub_metadata.description,
            epub_metadata=epub_metadata  # Store full metadata
        )
        
        # Build items map for content lookup
        all_items = list(book.get_items())
        items_map = {}
        for item in all_items:
            if item.get_type() == ITEM_DOCUMENT:
                # Store with multiple key variations for lookup
                name = item.get_name()
                items_map[name] = item
                # Also store just the filename
                if '/' in name:
                    items_map[name.split('/')[-1]] = item
        
        # Try TOC-based parsing first (same as PDF converters)
        if self.use_toc:
            toc_entries = self._extract_toc(book)
            if toc_entries:
                logger.info("Using TOC-based parsing (%d entries)", len(toc_entries))
                return self._parse_from_toc(book, structure, toc_entries, items_map)
            else:
                logger.info("No TOC found, falling back to HTML-based parsing")
        
        # Get spine order
        spine_ids = [item[0] for item in book.spine]
        logger.info("Spine contains %d items: %s", len(spine_ids), spine_ids[:10])
        
        # Process all document items
        sections: List[Dict[str, Any]] = []
        order = 0
        all_items = list(book.get_items())
        doc_items = [i for i in all_items if i.get_type() == ITEM_DOCUMENT]
        logger.info("Found %d total items, %d documents", len(all_items), len(doc_items))
        
        for item in all_items:
            if item.get_type() != ITEM_DOCUMENT:
                continue
            
            # Skip items not in spine
            item_id = item.get_id()
            if item_id not in spine_ids:
                logger.debug("Skipping item not in spine: %s", item_id)
                continue
            
            filename = item.get_name()
            html_content = item.get_content().decode('utf-8', errors='ignore')
            content_length = len(html_content)
            logger.info("Processing item: %s (id=%s, %d chars)", filename, item_id, content_length)
            
            # Try to split by headings first (for EPUBs with multiple chapters per file)
            split_sections = self._split_by_headings(html_content, filename)
            
            if split_sections:
                # Multiple sections found in this file
                logger.info("Split %s into %d sections", filename, len(split_sections))
                spine_order_base = spine_ids.index(item_id) if item_id in spine_ids else order
                for idx, split_sec in enumerate(split_sections):
                    logger.info("  - Section: '%s' (%s, %d words)",
                               split_sec['title'][:50], split_sec['type'], split_sec['word_count'])
                    sections.append({
                        'type': split_sec['type'],
                        'title': split_sec['title'],
                        'content': split_sec['content'],
                        'html_content': split_sec['html_content'],
                        'word_count': split_sec['word_count'],
                        'order': order,
                        'spine_order': spine_order_base + (idx * 0.01)  # Preserve order within file
                    })
                    order += 1
            else:
                # Single section - process normally
                logger.info("Processing as single section: %s", filename)
                text_content = self._extract_text(html_content)
                
                # Skip empty or very short content
                word_count = len(text_content.split())
                if word_count < 10:
                    logger.info("Skipping %s - too short (%d words)", filename, word_count)
                    continue
                
                # Extract and classify title
                extracted_title = self._extract_title_from_html(html_content)
                section_type = self._classify_section(
                    extracted_title or '',
                    os.path.basename(filename)
                )
                
                if section_type == 'skip':
                    logger.info("Skipping section (skip pattern): %s - '%s'", filename, extracted_title)
                    continue
                
                # Determine final title
                final_title = extracted_title or os.path.splitext(os.path.basename(filename))[0]
                logger.info("  Added section: '%s' (%s, %d words)", final_title[:50], section_type, word_count)
                
                sections.append({
                    'type': section_type,
                    'title': final_title,
                    'content': text_content,
                    'html_content': html_content,
                    'word_count': word_count,
                    'order': order,
                    'spine_order': spine_ids.index(item_id) if item_id in spine_ids else order
                })
                order += 1
        
        # Sort by spine order
        sections.sort(key=lambda x: x['spine_order'])
        logger.info("Total sections found before assignment: %d", len(sections))
        
        # Assign sections to structure
        chapter_count = 0
        total_words = 0
        subsection_order = 10000  # Start subsections with high order to not conflict
        
        for idx, sec in enumerate(sections):
            section = Section(
                type=sec['type'],
                title=sec['title'],
                content=sec['content'],
                html_content=sec['html_content'],
                order=sec['order'],
                word_count=sec['word_count'],
                level=1  # Top-level sections are level 1
            )
            total_words += section.word_count
            
            # Extract sub-sections from the HTML content
            subsections = self._extract_subsections(
                sec['html_content'],
                parent_order=sec['order'],
                start_order=subsection_order
            )
            if subsections:
                section.children = subsections
                subsection_order += len(subsections)
                # Add subsection words to total (they're part of parent content)
            
            if sec['type'] == 'prologue' and structure.prologue is None:
                structure.prologue = section
            elif sec['type'] == 'epilogue' and structure.epilogue is None:
                structure.epilogue = section
            elif sec['type'] == 'foreword' and structure.foreword is None:
                structure.foreword = section
            elif sec['type'] == 'afterword' and structure.afterword is None:
                structure.afterword = section
            elif sec['type'] == 'introduction' and structure.introduction is None:
                structure.introduction = section
            elif sec['type'] == 'chapter' or (sec['type'] == 'unknown' and sec['word_count'] >= self.min_chapter_words):
                # Treat as chapter if it's labeled as such or has enough content
                chapter_count += 1
                section.type = 'chapter'
                if sec['type'] == 'unknown':
                    section.title = f"Chapter {chapter_count}" if not section.title else section.title
                structure.chapters.append(section)
            elif sec['type'] == 'unknown' and sec['word_count'] < self.min_chapter_words:
                structure.other_sections.append(section)
        
        structure.total_word_count = total_words
        
        logger.info(
            "Parsing complete: %d chapters, prologue=%s, epilogue=%s, total words=%d",
            len(structure.chapters),
            structure.prologue is not None,
            structure.epilogue is not None,
            total_words
        )
        
        return structure

    def extract_content_dual(
        self,
        epub_source: Union[str, Path, BinaryIO],
        sections: List[Dict[str, Any]],
        custom_filter_config: Optional[Dict[str, Any]] = None
    ) -> tuple:
        """
        Extract both original and filtered content for selected sections.
        
        Used for the validation workflow where users need to see both versions.
        
        Args:
            epub_source: Path to EPUB file or file-like object
            sections: List of section dicts with 'href' and 'selected' fields
            custom_filter_config: Optional custom filter configuration from document
            
        Returns:
            Tuple of (original_sections, filtered_sections)
        """
        logger.info("Extracting dual content (original + filtered) for %d sections", len(sections))
        
        # Read EPUB file
        if isinstance(epub_source, (str, Path)):
            book = epub.read_epub(str(epub_source))
        else:
            with tempfile.NamedTemporaryFile(suffix='.epub', delete=False) as tmp:
                tmp.write(epub_source.read())
                tmp_path = tmp.name
            try:
                book = epub.read_epub(tmp_path)
            finally:
                os.unlink(tmp_path)
        
        # Build items map
        items_map = {}
        for item in book.get_items():
            if item.get_type() == ITEM_DOCUMENT:
                name = item.get_name()
                items_map[name] = item
                if '/' in name:
                    items_map[name.split('/')[-1]] = item
        
        def extract_raw_text(html_content: str) -> str:
            """Extract text WITHOUT filtering (original)."""
            parser = HTMLTextExtractor(apply_filters=False)
            try:
                parser.feed(html_content)
                return parser.get_text()
            except Exception as e:
                logger.warning("HTML parsing error (raw): %s", e)
                text = re.sub(r'<[^>]+>', ' ', html_content)
                return re.sub(r'\s+', ' ', text).strip()
        
        def extract_filtered_text(html_content: str) -> str:
            """Extract text WITH filtering (using custom or default config)."""
            parser = HTMLTextExtractor(apply_filters=True, custom_config=custom_filter_config)
            try:
                parser.feed(html_content)
                return parser.get_text()
            except Exception as e:
                logger.warning("HTML parsing error (filtered): %s", e)
                text = re.sub(r'<[^>]+>', ' ', html_content)
                return re.sub(r'\s+', ' ', text).strip()
        
        # Cache for full HTML content per file
        html_cache: Dict[str, str] = {}
        
        def get_full_html(base_href: str) -> str:
            """Get full HTML content for a file (cached)."""
            if base_href in html_cache:
                return html_cache[base_href]
            
            item = items_map.get(base_href)
            if not item:
                short_href = base_href.split('/')[-1] if '/' in base_href else base_href
                item = items_map.get(short_href)
            
            if not item:
                return ""
            
            try:
                content = item.get_content().decode('utf-8', errors='ignore')
                html_cache[base_href] = content
                return content
            except Exception:
                return ""
        
        # First pass: collect all section anchors to determine boundaries
        # Build a map of file -> sorted anchor list
        def collect_all_anchors(secs: List[Dict[str, Any]], anchors: List[tuple]):
            """Recursively collect all (file, anchor, order) tuples."""
            for s in secs:
                href = s.get('href', '')
                if href:
                    if '#' in href:
                        base, anchor = href.split('#', 1)
                    else:
                        base, anchor = href, None
                    anchors.append((base, anchor, s.get('order', 0)))
                if s.get('children'):
                    collect_all_anchors(s['children'], anchors)
        
        all_anchors: List[tuple] = []
        collect_all_anchors(sections, all_anchors)
        
        # Group by file and sort by anchor (extract numeric part for sorting)
        file_anchors: Dict[str, List[tuple]] = {}
        for base, anchor, order in all_anchors:
            if base not in file_anchors:
                file_anchors[base] = []
            file_anchors[base].append((anchor, order))
        
        # Sort anchors within each file by numeric part (p16 -> 16, p18 -> 18)
        def anchor_sort_key(item):
            anchor, order = item
            if anchor:
                # Extract numeric part from anchor like "p16" -> 16
                nums = re.findall(r'\d+', anchor)
                if nums:
                    return int(nums[0])
            return order
        
        for base, anchors_list in file_anchors.items():
            anchors_list.sort(key=anchor_sort_key)
        
        # Build anchor -> next_anchor map for slicing
        anchor_boundaries: Dict[str, Optional[str]] = {}  # "file#anchor" -> next_anchor
        for base, anchors_list in file_anchors.items():
            for i, (anchor, order) in enumerate(anchors_list):
                key = f"{base}#{anchor}" if anchor else base
                if i + 1 < len(anchors_list):
                    next_anchor = anchors_list[i + 1][0]
                    anchor_boundaries[key] = next_anchor
                else:
                    anchor_boundaries[key] = None  # Last section - goes to end of file
        
        logger.info("Built anchor boundaries for %d sections across %d files", 
                   len(anchor_boundaries), len(file_anchors))
        
        def slice_html_by_anchor(full_html: str, start_anchor: Optional[str], 
                                  end_anchor: Optional[str]) -> str:
            """Extract HTML content between two anchors."""
            if not start_anchor:
                # No start anchor - return from beginning
                if end_anchor:
                    # Find end anchor and return up to it
                    end_patterns = [
                        f'<a[^>]*id=["\']?{re.escape(end_anchor)}["\']?',
                        f'id=["\']?{re.escape(end_anchor)}["\']?'
                    ]
                    for pattern in end_patterns:
                        match = re.search(pattern, full_html, re.IGNORECASE)
                        if match:
                            return full_html[:match.start()]
                return full_html
            
            # Find start anchor position
            start_patterns = [
                f'<a[^>]*id=["\']?{re.escape(start_anchor)}["\']?[^>]*>',
                f'<[^>]+id=["\']?{re.escape(start_anchor)}["\']?[^>]*>'
            ]
            
            start_pos = None
            for pattern in start_patterns:
                match = re.search(pattern, full_html, re.IGNORECASE)
                if match:
                    start_pos = match.start()
                    break
            
            if start_pos is None:
                logger.debug("Start anchor '%s' not found in HTML", start_anchor)
                return full_html  # Fallback to full content
            
            # Find end anchor position (if exists)
            if end_anchor:
                end_patterns = [
                    f'<a[^>]*id=["\']?{re.escape(end_anchor)}["\']?',
                    f'<[^>]+id=["\']?{re.escape(end_anchor)}["\']?'
                ]
                
                for pattern in end_patterns:
                    # Search from after start position
                    match = re.search(pattern, full_html[start_pos + 1:], re.IGNORECASE)
                    if match:
                        end_pos = start_pos + 1 + match.start()
                        sliced = full_html[start_pos:end_pos]
                        logger.debug("Sliced HTML from '%s' to '%s': %d chars", 
                                   start_anchor, end_anchor, len(sliced))
                        return sliced
            
            # No end anchor - return from start to end of file
            sliced = full_html[start_pos:]
            logger.debug("Sliced HTML from '%s' to end: %d chars", start_anchor, len(sliced))
            return sliced
        
        def get_html_content(href: str) -> str:
            """Get HTML content for a TOC entry, sliced by anchor boundaries."""
            if not href:
                return ""
            
            if '#' in href:
                base_href, anchor = href.split('#', 1)
            else:
                base_href, anchor = href, None
            
            full_html = get_full_html(base_href)
            if not full_html:
                return ""
            
            # Get next anchor for slicing
            key = f"{base_href}#{anchor}" if anchor else base_href
            next_anchor = anchor_boundaries.get(key)
            
            # Slice HTML between current anchor and next anchor
            sliced_html = slice_html_by_anchor(full_html, anchor, next_anchor)
            
            return sliced_html
        
        def extract_for_section_dual(section: Dict[str, Any]) -> tuple:
            """Extract both original and filtered content for a section."""
            original = section.copy()
            filtered = section.copy()
            
            if section.get('selected', False) and section.get('href'):
                html_content = get_html_content(section['href'])
                
                if html_content:
                    # Original (unfiltered)
                    original_text = extract_raw_text(html_content)
                    original['content'] = original_text
                    original['word_count'] = len(original_text.split())
                    original['html_content'] = html_content
                    
                    # Filtered
                    filtered_text = extract_filtered_text(html_content)
                    filtered['content'] = filtered_text
                    filtered['word_count'] = len(filtered_text.split())
                    filtered['html_content'] = html_content
                    
                    logger.info("Dual extracted: %s (orig: %d, filt: %d words)",
                               section.get('title', '')[:40],
                               original['word_count'], filtered['word_count'])
            
            # Process children recursively
            if 'children' in section and section['children']:
                orig_children = []
                filt_children = []
                for child in section['children']:
                    orig_child, filt_child = extract_for_section_dual(child)
                    orig_children.append(orig_child)
                    filt_children.append(filt_child)
                original['children'] = orig_children
                filtered['children'] = filt_children
            
            return original, filtered
        
        original_sections = []
        filtered_sections = []
        
        for s in sections:
            orig, filt = extract_for_section_dual(s)
            original_sections.append(orig)
            filtered_sections.append(filt)
        
        # Count extracted
        def count_with_content(secs):
            count = sum(1 for s in secs if s.get('content'))
            for s in secs:
                if s.get('children'):
                    count += count_with_content(s['children'])
            return count
        
        orig_count = count_with_content(original_sections)
        filt_count = count_with_content(filtered_sections)
        logger.info("Dual extraction complete: %d original, %d filtered sections", orig_count, filt_count)
        
        return original_sections, filtered_sections

    def parse_to_json(
        self,
        epub_source: Union[str, Path, BinaryIO],
        output_path: Optional[Union[str, Path]] = None,
        include_html: bool = False,
        content_as_html: bool = False,
        indent: int = 2
    ) -> str:
        """
        Parse EPUB and return/save JSON.
        
        Args:
            epub_source: Path to EPUB or file-like object.
            output_path: Optional path to save JSON output.
            include_html: Whether to include raw HTML content alongside text content.
            content_as_html: Whether to output content as HTML instead of plain text.
            indent: JSON indentation level.
            
        Returns:
            JSON string of parsed content.
        """
        structure = self.parse(epub_source)
        json_output = structure.to_json(include_html=include_html, content_as_html=content_as_html, indent=indent)
        
        if output_path:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(json_output)
            logger.info("JSON saved to: %s", output_path)
        
        return json_output


def parse_epub(
    epub_path: Union[str, Path],
    output_json_path: Optional[Union[str, Path]] = None,
    include_html: bool = False,
    content_as_html: bool = False
) -> str:
    """
    Convenience function to parse an EPUB file.
    
    Args:
        epub_path: Path to the EPUB file.
        output_json_path: Optional path to save JSON output.
        include_html: Whether to include HTML content alongside text.
        content_as_html: Whether to output content as HTML instead of plain text.
        
    Returns:
        JSON string with parsed structure.
        
    Example:
        >>> json_str = parse_epub('book.epub', 'book.json')
        >>> # Or without saving:
        >>> json_str = parse_epub('book.epub')
        >>> # Output as HTML:
        >>> json_str = parse_epub('book.epub', content_as_html=True)
    """
    parser = EpubParser()
    return parser.parse_to_json(epub_path, output_json_path, include_html, content_as_html)


# CLI support
if __name__ == '__main__':
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description='Parse EPUB files and extract structured content as JSON',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python epub_parser.py input.epub                     # Print JSON to stdout
    python epub_parser.py input.epub output.json         # Save to file
    python epub_parser.py input.epub -o book_structure.json
    python epub_parser.py input.epub --html              # Content as HTML
    python epub_parser.py input.epub --include-html      # Include both text and HTML
    python epub_parser.py input.epub --compact           # No indentation
    
Output Structure:
    {
        "metadata": {
            "title": "Book Title",
            "author": "Author Name",
            "totalWordCount": 50000,
            "chapterCount": 20
        },
        "structure": {
            "hasPrologue": true,
            "hasEpilogue": true,
            ...
        },
        "prologue": { "title": "...", "content": "...", "wordCount": 500 },
        "chapters": [
            { "title": "Chapter 1", "content": "...", "wordCount": 2500 },
            ...
        ],
        "epilogue": { "title": "...", "content": "...", "wordCount": 300 }
    }
        """
    )
    parser.add_argument('input', help='Input EPUB file path')
    parser.add_argument('output', nargs='?', help='Output JSON file path (optional, prints to stdout if not provided)')
    parser.add_argument('-o', '--output-file', help='Output JSON file path')
    parser.add_argument('--html', action='store_true', help='Output content as HTML instead of plain text')
    parser.add_argument('--include-html', action='store_true', help='Include both text and HTML content')
    parser.add_argument('--compact', action='store_true', help='Output compact JSON (no indentation)')
    parser.add_argument('--min-words', type=int, default=100, help='Minimum words to consider content as a chapter (default: 100)')
    parser.add_argument('--include-captions', action='store_true', help='Include image captions (skipped by default)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose logging')

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # Determine output path
    output = args.output or args.output_file

    # Parse
    try:
        epub_parser = EpubParser(
            min_chapter_words=args.min_words,
            skip_captions=not args.include_captions
        )
        indent = None if args.compact else 2
        
        json_output = epub_parser.parse_to_json(
            args.input,
            output_path=output,
            include_html=args.include_html,
            content_as_html=args.html,
            indent=indent or 0 if args.compact else 2
        )
        
        if not output:
            # Print to stdout
            print(json_output)
        else:
            print(f"Successfully parsed: {args.input} -> {output}", file=sys.stderr)
            
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.exception("Parsing failed: %s", str(e))
        sys.exit(1)

