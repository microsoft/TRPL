# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

class CitationStreamer:
    """Strip [N] / [N, M] citation markers from streamed text and collect indices.

    Accepts [1], [12], [1, 3], [1,3,5]; anything else inside [] is emitted
    as-is. Indices preserve first-appearance order and are de-duplicated.
    """

    _MAX_BUF = 64  # give up after this many chars without a closing ']'

    def __init__(self) -> None:
        self._buf = ""
        self._in_marker = False
        self._indices: list[int] = []
        self._seen: set[int] = set()

    @property
    def indices(self) -> list[int]:
        return list(self._indices)

    def feed(self, s: str) -> str:
        out: list[str] = []
        for ch in s:
            if not self._in_marker:
                if ch == "[":
                    self._in_marker = True
                    self._buf = "["
                else:
                    out.append(ch)
                continue

            self._buf += ch
            if ch == "]":
                parsed = _try_parse_indices(self._buf[1:-1])
                if parsed is not None:
                    for n in parsed:
                        if n not in self._seen:
                            self._seen.add(n)
                            self._indices.append(n)
                else:
                    out.append(self._buf)
                self._buf = ""
                self._in_marker = False
            elif ch == "[":
                # New bracket opens before the previous one closed; flush the
                # previous buffer minus this char as plain text, and start fresh.
                out.append(self._buf[:-1])
                self._buf = "["
            elif len(self._buf) >= self._MAX_BUF:
                out.append(self._buf)
                self._buf = ""
                self._in_marker = False
        return "".join(out)

    def flush(self) -> str:
        """Drain any buffered chars at end of stream (e.g. unterminated '[1')."""
        out = self._buf
        self._buf = ""
        self._in_marker = False
        return out


def _try_parse_indices(inner: str) -> list[int] | None:
    s = inner.strip()
    if not s:
        return None
    parts = [p.strip() for p in s.split(",")]
    out: list[int] = []
    for p in parts:
        if not p.isdigit():
            return None
        out.append(int(p))
    return out
