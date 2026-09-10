"""Regression tests for VectorizedLRUCache bugs (REPORT.md #9, #10, #29).

Each test reproduces the exact divergence/failure mode that the in-code
comments document, then asserts the cache behaves correctly. Failing
versions of these would corrupt cached entries, lose persisted writes, or
leak slot bookkeeping across reloads.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from chatbot.VectorSearch.cache import VectorizedLRUCache


# -----------------------------
# Helpers
# -----------------------------

def _unit_vec(dim: int, *, hot_index: int) -> list[float]:
    """A unit vector that's '1' along one axis. Gives us full control over
    cosine similarity rankings between entries and the query."""
    v = np.zeros(dim, dtype=np.float32)
    v[hot_index] = 1.0
    return v.tolist()


def _make_cache(tmp_path: Path, *, dim: int = 4, capacity: int = 4) -> VectorizedLRUCache:
    """Small cache with a temp persistence file so flushes don't escape the test."""
    return VectorizedLRUCache(
        dim=dim,
        capacity=capacity,
        threshold=0.5,  # generous so test embeddings score over it
        persist_path=str(tmp_path / "kb.json"),
        flush_interval_sec=999.0,  # block automatic interval flushes
        flush_every_n=999,  # block automatic count flushes
    )


# -----------------------------
# Bug #9: semantic_lookup key/slot alignment
# -----------------------------

class TestSemanticLookupAlignment:
    """The slots-vs-keys alignment fix in cache.py:194-219."""

    def test_returns_correct_key_when_one_filtered_key_is_missing_from_slot_map(
        self, tmp_path
    ):
        """REPORT.md #9: a key surviving the constraint filter but missing
        from _key_to_slot must NOT shift the argmax->key mapping.

        Setup:
        - Insert A, B, C all in the same bucket. B's embedding is closest to
          the query, C's is second, A's is third.
        - Remove B from _key_to_slot only (simulate the original out-of-sync
          state). B stays in self.cache, lru_order and _bucket_index, so it
          still passes the metadata filter.
        - The lookup must skip B (no slot) and return C, the highest-scoring
          key that IS aligned. The bug would have returned a different key
          because argmax indexed into filtered_keys directly instead of the
          shorter aligned_keys.
        """
        cache = _make_cache(tmp_path, dim=4)

        # A on axis 0, B on axis 1, C on axis 2.
        cache.set_entry("A", "result-A", _unit_vec(4, hot_index=0), bucket_id="b1")
        cache.set_entry("B", "result-B", _unit_vec(4, hot_index=1), bucket_id="b1")
        cache.set_entry("C", "result-C", _unit_vec(4, hot_index=2), bucket_id="b1")

        # Simulate the out-of-sync state: B's slot mapping vanishes but B is
        # still considered a candidate by the bucket / metadata filter.
        cache._key_to_slot.pop("B")

        # Build the query as a unit vector pre-normalized so the resulting
        # scores match raw component values. Direction is mostly along axis
        # 1 (B) with a smaller-but-still-above-threshold pull toward axis 2
        # (C). After B is desynced the alignment-correct answer is C.
        q = np.array([0.0, 0.85, 0.7, 0.0], dtype=np.float32)
        q /= np.linalg.norm(q)
        query = q.tolist()

        result = cache.semantic_lookup(query, bucket_id="b1")

        assert result is not None
        best_key, best_score, _tokens = result
        assert best_key == "C", (
            f"Expected C (best aligned-key after B is desynced); got {best_key}. "
            "Without the alignment fix, argmax against [A, C] scores returns "
            "the wrong filtered_keys entry."
        )
        # The score must equal the (pre-normalized) component along axis 2,
        # i.e. C's dot product. Not zero (which would be A's), not the B
        # value, not garbage from a row index mismatch.
        assert best_score == pytest.approx(float(q[2]), abs=1e-5)

    def test_returns_none_when_all_filtered_keys_are_unaligned(self, tmp_path):
        """If every surviving candidate is missing from _key_to_slot, the
        lookup must return None rather than crashing on an empty matrix."""
        cache = _make_cache(tmp_path, dim=4)
        cache.set_entry("A", "rA", _unit_vec(4, hot_index=0), bucket_id="b1")
        cache.set_entry("B", "rB", _unit_vec(4, hot_index=1), bucket_id="b1")

        cache._key_to_slot.pop("A")
        cache._key_to_slot.pop("B")

        assert cache.semantic_lookup([1.0, 0.0, 0.0, 0.0], bucket_id="b1") is None


# -----------------------------
# Bug #10: flush state preserved when disk write fails
# -----------------------------

class TestFlushDurability:
    """cache.py:329-395: dirty/_set_since_flush must survive a failed flush."""

    def test_disk_write_failure_keeps_dirty_flag_and_counter(self, tmp_path, monkeypatch):
        """REPORT.md #10: if _flush_to_disk fails (e.g. open() raises), the
        cache must NOT zero _dirty or _set_since_flush. Otherwise the next
        successful flush would persist a stale view and silently drop the
        in-flight writes."""
        cache = _make_cache(tmp_path)

        # Build a known dirty state directly (set_entry would race with the
        # daemon flush thread; we want a deterministic pre-condition).
        cache._dirty = True
        cache._set_since_flush = 5

        # Make open() blow up so the tmp write fails before os.replace.
        import builtins
        real_open = builtins.open

        def failing_open(path, *args, **kwargs):
            # Only fail the cache write; leave any other open() calls alone.
            if str(path).endswith(".tmp"):
                raise OSError("disk on fire")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", failing_open)

        # Call the flush directly with the matching snapshot_count so we
        # know what the decrement *would* have been on success.
        snapshot = {"items": []}
        cache._flush_to_disk(snapshot, 5)

        # Failure path: state must be untouched. The next successful flush
        # will then try again and persist the data.
        assert cache._dirty is True, "Disk failure must not clear the dirty flag"
        assert cache._set_since_flush == 5, (
            f"Counter dropped from 5 to {cache._set_since_flush} despite write failure"
        )
        # And no file was committed at the real persist path.
        assert not Path(cache.persist_path).exists()

    def test_disk_write_success_clears_state(self, tmp_path):
        """The companion success path: a normal flush DOES clear the flags.
        This guards against the test above passing because we'd broken the
        success path too."""
        cache = _make_cache(tmp_path)
        cache._dirty = True
        cache._set_since_flush = 3

        snapshot = {"items": []}
        cache._flush_to_disk(snapshot, 3)

        assert cache._dirty is False
        assert cache._set_since_flush == 0
        assert Path(cache.persist_path).exists()


# -----------------------------
# Bug #29: slot allocation fallback / load-path validation
# -----------------------------

class TestSlotAllocationSafety:
    """cache.py:278-289 (write) and :427-433 (load): never silently overwrite
    slot 0 when bookkeeping is corrupt, and never leak slots on bad load."""

    def test_set_entry_refuses_write_when_slots_empty_and_lru_empty(self, tmp_path, caplog):
        """REPORT.md #29 Goal A: drained _free_slots + empty lru_order means
        slot 0 is occupied by some prior entry whose bookkeeping is lost. The
        cache must refuse the write rather than overwrite slot 0 (and the
        entry living there)."""
        cache = _make_cache(tmp_path, dim=4, capacity=2)

        # Plant an entry directly into slot 0, then deliberately corrupt the
        # bookkeeping so the cache *thinks* it has nothing in lru_order but
        # still has slot 0 occupied with real data.
        cache._emb[0] = np.asarray(_unit_vec(4, hot_index=0), dtype=np.float32)
        cache._slot_to_key[0] = "orphan"
        cache.cache["orphan"] = {"result": "DO NOT OVERWRITE", "metadata": {}, "ts": 0.0}
        # Drain free_slots and leave lru_order empty.
        cache._free_slots.clear()
        cache.lru_order.clear()
        # Note: _key_to_slot intentionally NOT set for "orphan" — that's the
        # exact desync that originally let writes silently land on slot 0.

        with caplog.at_level("ERROR"):
            cache.set_entry("newkey", "new-value", _unit_vec(4, hot_index=1))

        # Refusal: the new key must not be present anywhere.
        assert "newkey" not in cache.cache
        assert "newkey" not in cache._key_to_slot
        assert "newkey" not in cache.lru_order
        # Slot 0's payload survives.
        assert cache.cache["orphan"]["result"] == "DO NOT OVERWRITE"
        # And the embedding wasn't blown away by a write to slot 0.
        np.testing.assert_array_equal(
            cache._emb[0], np.asarray(_unit_vec(4, hot_index=0), dtype=np.float32)
        )
        # Operator gets a log line they can grep for.
        assert any("no free slots" in r.message.lower() for r in caplog.records), (
            f"Expected a 'no free slots' error log, got: {[r.message for r in caplog.records]}"
        )

    def test_set_entry_still_evicts_normally_when_lru_has_entries(self, tmp_path):
        """The refusal above must not regress the normal eviction path."""
        cache = _make_cache(tmp_path, dim=4, capacity=2)
        cache.set_entry("A", "rA", _unit_vec(4, hot_index=0))
        cache.set_entry("B", "rB", _unit_vec(4, hot_index=1))
        # Both slots used; lru_order = [A, B]. C should evict A.
        cache.set_entry("C", "rC", _unit_vec(4, hot_index=2))
        assert "A" not in cache.cache
        assert "B" in cache.cache
        assert "C" in cache.cache

    def test_load_path_skips_bad_embedding_without_leaking_slot(self, tmp_path):
        """REPORT.md #29 Goal B: a malformed embedding (wrong dim) on disk
        must be skipped BEFORE _free_slots.pop(). Without that ordering, the
        slot would leak: popped but never bound to a key. Repeated bad loads
        would drain _free_slots and force later writes onto slot 0."""
        dim = 4
        capacity = 3
        persist_path = tmp_path / "kb.json"

        # Persistence file with one valid entry (dim=4) and one corrupt
        # entry (dim=3). The corrupt entry historically leaked its slot.
        persist_path.write_text(json.dumps({
            "items": [
                {
                    "key": "good",
                    "val": {
                        "result": "ok",
                        "emb": [1.0, 0.0, 0.0, 0.0],  # correct shape
                        "metadata": {},
                        "ts": 0.0,
                    },
                },
                {
                    "key": "bad",
                    "val": {
                        "result": "x",
                        "emb": [1.0, 0.0, 0.0],  # WRONG dim -> must skip
                        "metadata": {},
                        "ts": 0.0,
                    },
                },
            ]
        }))

        cache = VectorizedLRUCache(
            dim=dim,
            capacity=capacity,
            threshold=0.5,
            persist_path=str(persist_path),
            flush_interval_sec=999.0,
            flush_every_n=999,
        )

        # "good" loaded, "bad" skipped.
        assert "good" in cache.cache
        assert "bad" not in cache.cache
        assert "good" in cache._key_to_slot

        # Slot leak check: capacity=3, one entry loaded, so 2 free slots
        # must remain. If shape-validation ran AFTER pop, the bad entry
        # would have eaten a slot and we'd see only 1 free.
        assert len(cache._free_slots) == capacity - 1, (
            f"Expected {capacity - 1} free slots after one valid load + one "
            f"shape-invalid entry; got {len(cache._free_slots)}. "
            "The shape-invalid entry leaked a slot."
        )
