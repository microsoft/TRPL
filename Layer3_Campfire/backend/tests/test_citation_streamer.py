from chatbot.utils.citation_streamer import CitationStreamer


def _feed_chunks(chunks: list[str]) -> tuple[str, list[int]]:
    s = CitationStreamer()
    text = "".join(s.feed(c) for c in chunks)
    text += s.flush()
    return text, s.indices


def test_strips_single_marker_and_captures_index():
    text, idx = _feed_chunks(["He rode camels [3] in Egypt."])
    assert text == "He rode camels  in Egypt."
    assert idx == [3]


def test_marker_split_across_chunks():
    text, idx = _feed_chunks(["He rode camels [", "3", "] in Egypt."])
    assert text == "He rode camels  in Egypt."
    assert idx == [3]


def test_multi_digit_index():
    text, idx = _feed_chunks(["See [12]."])
    assert text == "See ."
    assert idx == [12]


def test_comma_separated_indices():
    text, idx = _feed_chunks(["Supported by [1, 3, 5]."])
    assert text == "Supported by ."
    assert idx == [1, 3, 5]


def test_adjacent_markers_dedupe_preserve_order():
    text, idx = _feed_chunks(["x [3][1][3] y"])
    assert text == "x  y"
    assert idx == [3, 1]


def test_non_citation_bracket_text_passes_through():
    text, idx = _feed_chunks(["a [see footnote] b"])
    assert text == "a [see footnote] b"
    assert idx == []


def test_empty_brackets_pass_through():
    text, idx = _feed_chunks(["a [] b"])
    assert text == "a [] b"
    assert idx == []


def test_nested_brackets_treated_as_text():
    # When a new '[' opens before the prior closes, prior buffer flushes as text.
    text, idx = _feed_chunks(["a [[1]] b"])
    assert "[1]" not in text  # the inner [1] is captured
    assert idx == [1]


def test_unterminated_marker_flushes_on_close():
    s = CitationStreamer()
    out = s.feed("trailing [1")
    out += s.flush()
    assert out == "trailing [1"
    assert s.indices == []


def test_buffer_overflow_emits_as_text():
    long_inner = "x" * 80  # exceeds _MAX_BUF
    text, idx = _feed_chunks([f"a [{long_inner}] b"])
    assert long_inner in text
    assert idx == []


def test_one_char_at_a_time():
    s = CitationStreamer()
    text = "".join(s.feed(c) for c in "Hello [1] world [2, 3].")
    text += s.flush()
    assert text == "Hello  world ."
    assert s.indices == [1, 2, 3]


def test_invalid_index_format_passes_through():
    # Non-digit content inside brackets is not a citation.
    text, idx = _feed_chunks(["see [Source 1]"])
    assert text == "see [Source 1]"
    assert idx == []
