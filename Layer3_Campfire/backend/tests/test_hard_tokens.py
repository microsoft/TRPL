# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Regression tests for hard-token extraction (REPORT.md #24).

The semantic cache uses hard tokens as a lexical gate on top of cosine
similarity. If two queries about distinct identifiers (ABC-123 vs DEF-123)
produce the same hard-token set, they can share cache entries — returning
one identifier's answer for a query about another.
"""

from chatbot.VectorSearch.utils import extract_hard_tokens


def test_hyphenated_letter_digit_identifier_captured_whole():
    """`ABC-123` is one identifier, not the digit `123` with a prefix dropped."""
    tokens = extract_hard_tokens("What is in ABC-123?")
    assert "ABC-123" in tokens
    # The bare digit suffix must not also be a separate token, since the
    # regex returns non-overlapping matches and we want the full identifier.
    assert "123" not in tokens


def test_distinct_hyphenated_identifiers_do_not_collide():
    """ABC-123 and DEF-123 must produce different hard-token sets."""
    assert extract_hard_tokens("about ABC-123") != extract_hard_tokens("about DEF-123")


def test_letter_digit_without_separator_still_extracted():
    """`PROJ1234` (no separator) is captured by the same pattern."""
    assert "PROJ1234" in extract_hard_tokens("ticket PROJ1234")


def test_multi_part_hyphenated_identifier_kept_whole():
    """`PROJ-1234-5678` is captured as one long token by the 8+ char pattern."""
    tokens = extract_hard_tokens("see PROJ-1234-5678")
    assert "PROJ-1234-5678" in tokens
    # The sub-digit-runs must not appear as extra tokens.
    assert "1234" not in tokens
    assert "5678" not in tokens


def test_plain_digit_run_remains_a_hard_token():
    """Three-or-more digit runs without surrounding letters still gate the cache."""
    assert "1234" in extract_hard_tokens("clause 1234")


def test_short_word_without_digits_is_not_a_token():
    """Common short prose words must not be treated as identifiers."""
    assert extract_hard_tokens("the cat sat") == ()


def test_multiple_identifiers_in_one_query():
    """All identifiers in a sentence are extracted, sorted and deduplicated."""
    tokens = extract_hard_tokens("compare ABC-123 with DEF-456")
    assert "ABC-123" in tokens
    assert "DEF-456" in tokens
