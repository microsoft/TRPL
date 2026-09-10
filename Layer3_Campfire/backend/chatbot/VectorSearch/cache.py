# -*- coding: utf-8 -*-
import logging
import os
import threading
import time
import json
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from .utils import to_jsonable

logger = logging.getLogger(__name__)

# =========================
# Embedding LRU (Reduces Embedding API calls/fluctuation)
# =========================

class EmbeddingLRUCache:
    def __init__(self, capacity: int = 2000) -> None:
        self.capacity = capacity
        self.lock = threading.Lock()
        self.cache: Dict[str, List[float]] = {}
        self.order: List[str] = []  # 0 is oldest

    def get(self, key: str) -> Optional[List[float]]:
        with self.lock:
            if key not in self.cache:
                return None
            # Maintain LRU order
            self.order.remove(key)
            self.order.append(key)
            return self.cache[key]

    def set(self, key: str, val: List[float]) -> None:
        with self.lock:
            if key in self.cache:
                self.order.remove(key)
            elif len(self.order) >= self.capacity:
                old = self.order.pop(0)
                self.cache.pop(old, None)
            self.cache[key] = val
            self.order.append(key)


# =========================
# Industrial-grade Vectorized LRU Cache
# (Result Cache + Semantic Hit + Persistence)
# =========================

class VectorizedLRUCache:
    """
    Result Caching Structure:
    - exact key hit: O(1)
    - semantic_lookup: Vector similarity match within a "bucket" (constraint space) + Hard Token Gating
    - Supports disk persistence with flush intervals to reduce IO jitter.
    """

    def __init__(
        self,
        dim: int = 3072,
        capacity: int = 500,
        threshold: float = 0.93,
        persist_path: str = "kb_search_lru.json",
        flush_interval_sec: float = 3.0,
        flush_every_n: int = 10,
    ) -> None:
        self.dim = dim
        self.capacity = capacity
        self.threshold = threshold
        self.persist_path = Path(persist_path)

        self.flush_interval_sec = float(flush_interval_sec)
        self.flush_every_n = int(flush_every_n)

        self.lock = threading.Lock()
        # Serializes disk flushes so they run outside the cache lock without racing each other.
        self._flush_lock = threading.Lock()

        # Pre-allocate matrix with slots to avoid rebuilding matrix on every update
        self._emb = np.zeros((capacity, dim), dtype=np.float32)
        self._key_to_slot: Dict[str, int] = {}
        self._slot_to_key: List[Optional[str]] = [None] * capacity
        self._free_slots: List[int] = list(range(capacity - 1, -1, -1))  # Stack of free slots

        # Result storage: key -> {result, emb, metadata, ts}
        self.cache: Dict[str, Dict[str, Any]] = {}

        # LRU Order: 0 is oldest
        self.lru_order: List[str] = []

        # Bucket Index: bucket_id -> set(keys), drastically reduces scan range for semantic lookup
        self._bucket_index: Dict[str, set] = {}

        # Persistence control
        self._dirty = False
        self._set_since_flush = 0
        self._last_flush_ts = 0.0

        self._load_from_disk()

    @staticmethod
    def _normalize(v: List[float]) -> np.ndarray:
        arr = np.asarray(v, dtype=np.float32)
        norm = np.linalg.norm(arr)
        return arr / (norm + 1e-9) if norm > 0 else arr

    @staticmethod
    def _hard_token_gate(query_tokens: Tuple[str, ...], cached_tokens: Tuple[str, ...]) -> bool:
        """
        Lexical Gate: Any hard token appearing in the query must also exist 
        in the cached candidate (Better to miss than to return wrong context).
        """
        if not query_tokens:
            return True
        s = set(cached_tokens or ())
        for t in query_tokens:
            if t not in s:
                return False
        return True

    def get(self, key: str) -> Optional[Any]:
        """Exact key match."""
        with self.lock:
            if key not in self.cache:
                return None
            # Move to end (newest)
            self.lru_order.remove(key)
            self.lru_order.append(key)
            return self.cache[key]["result"]

    def semantic_lookup(
        self, query_vec: List[float], **constraints
    ) -> Optional[Tuple[str, float, Tuple[str, ...]]]:
        """
        Semantic lookup:
        - constraints: Exact matching (bucket_id / retriever_sig / filters etc.)
        - Special key "__hard_tokens__": Used for subset gating

        Returns (best_key, score, cached_hard_tokens) or None. The cached
        hard_tokens are returned so the caller can re-store the result under a
        new exact_key without losing the entry's original token coverage.
        """
        if not query_vec:
            return None

        q_norm = self._normalize(query_vec)

        with self.lock:
            if not self.lru_order:
                return None

            # 1) Narrow down candidates using bucket_id
            bucket_id = constraints.get("bucket_id")
            if bucket_id is not None and bucket_id in self._bucket_index:
                candidate_keys = list(self._bucket_index[bucket_id])
            else:
                candidate_keys = list(self.lru_order)

            if not candidate_keys:
                return None

            # 2) Filter by constraints + Hard Token Gate
            q_hard_tokens: Tuple[str, ...] = constraints.get("__hard_tokens__", tuple())
            filtered_keys: List[str] = []

            logger.debug("extract hard tokens %s", q_hard_tokens)

            for k in candidate_keys:
                item = self.cache.get(k)
                if not item:
                    continue
                meta = item.get("metadata", {}) or {}

                # Hard Token Gate (Subset check)
                if not self._hard_token_gate(q_hard_tokens, meta.get("hard_tokens", tuple())):
                    logger.debug("hard token gate failed %s %s", k, meta.get("hard_tokens", tuple()))
                    continue
                
                ok = True
                for ck, cv in constraints.items():
                    if ck == "__hard_tokens__":
                        continue
                    if meta.get(ck) != cv:
                        ok = False
                        break
                if ok:
                    filtered_keys.append(k)

            if not filtered_keys:
                return None

            # 3) Assemble matrix and score
            # IMPORTANT: keep slots and the key list aligned. Any key missing
            # from _key_to_slot must be dropped from both lists in lockstep,
            # otherwise argmax against the shorter scores array indexes the
            # wrong key and the cache returns a different query's result.
            slots: List[int] = []
            aligned_keys: List[str] = []
            for k in filtered_keys:
                s = self._key_to_slot.get(k)
                if s is None:
                    continue
                slots.append(s)
                aligned_keys.append(k)

            if not slots:
                return None

            mat = self._emb[slots]  # [n, dim]
            scores = mat @ q_norm
            best_i = int(np.argmax(scores))
            best_score = float(scores[best_i])
            logger.debug("semantic lookup best score: %s", best_score)

            if best_score < self.threshold:
                return None

            best_key = aligned_keys[best_i]
            best_meta = (self.cache.get(best_key) or {}).get("metadata") or {}
            cached_hard_tokens = tuple(best_meta.get("hard_tokens") or ())
            return best_key, best_score, cached_hard_tokens

    def _evict_one_locked(self) -> bool:
        """Evict the oldest entry and return its slot to the free pool.
        Returns True if an entry was evicted, False if there was nothing to evict."""
        if not self.lru_order:
            return False
        old_key = self.lru_order.pop(0)
        old_item = self.cache.pop(old_key, None)

        if old_item:
            old_meta = old_item.get("metadata", {}) or {}
            old_bucket = old_meta.get("bucket_id")
            if old_bucket in self._bucket_index and old_key in self._bucket_index[old_bucket]:
                self._bucket_index[old_bucket].remove(old_key)
                if not self._bucket_index[old_bucket]:
                    self._bucket_index.pop(old_bucket, None)

        old_slot = self._key_to_slot.pop(old_key, None)
        if old_slot is not None:
            self._slot_to_key[old_slot] = None
            self._free_slots.append(old_slot)
        return True

    def set_entry(self, key: str, result: Any, q_emb: List[float], **metadata) -> None:
        """Write to cache."""
        if not q_emb:
            return

        v = self._normalize(q_emb)
        snapshot: Optional[Dict[str, Any]] = None
        snapshot_count: int = 0

        with self.lock:
            # Exists: Update + Move to end
            if key in self.cache:
                self.lru_order.remove(key)
                self.lru_order.append(key)

                slot = self._key_to_slot[key]
                self._emb[slot] = v
                # NOTE: the embedding lives only in self._emb[slot]. Storing
                # it again in self.cache[key]["emb"] would double the memory
                # footprint; the flush rebuilds the JSON list from the matrix.
                self.cache[key] = {
                    "result": result,
                    "metadata": metadata,
                    "ts": time.time(),
                }
                self._mark_dirty_locked()
                snapshot, snapshot_count = self._prepare_flush_locked()
            else:
                # New Entry: Need a slot. Evict the LRU entry until one is
                # available — never silently overwrite slot 0, which would
                # corrupt an existing entry if state got out of sync (e.g.
                # leaked slots from a partial load).
                while not self._free_slots and self.lru_order:
                    self._evict_one_locked()

                if not self._free_slots:
                    # Cache is structurally empty but has no free slots. This
                    # means slot bookkeeping is corrupt. Refuse the write
                    # rather than overwriting an arbitrary entry.
                    logger.error(
                        "VectorizedLRUCache: no free slots and lru_order empty; refusing to write key=%s",
                        key,
                    )
                    return

                slot = self._free_slots.pop()
                self._key_to_slot[key] = slot
                self._slot_to_key[slot] = key
                self._emb[slot] = v

                # Embedding lives in self._emb[slot] only; see the update
                # branch above for the rationale.
                self.cache[key] = {
                    "result": result,
                    "metadata": metadata,
                    "ts": time.time(),
                }
                self.lru_order.append(key)

                # Add to bucket index
                b = metadata.get("bucket_id")
                if b is not None:
                    self._bucket_index.setdefault(b, set()).add(key)

                self._mark_dirty_locked()
                snapshot, snapshot_count = self._prepare_flush_locked()

        # Disk I/O is offloaded to a daemon thread so the async caller isn't
        # blocked by json.dump and os.replace. _flush_lock serializes
        # concurrent flushes inside _flush_to_disk; the rate limit in
        # _prepare_flush_locked keeps the dispatch rate low.
        if snapshot is not None:
            threading.Thread(
                target=self._flush_to_disk,
                args=(snapshot, snapshot_count),
                name="VectorizedLRUCache-flush",
                daemon=True,
            ).start()

    def _mark_dirty_locked(self) -> None:
        self._dirty = True
        self._set_since_flush += 1

    def _prepare_flush_locked(self) -> Tuple[Optional[Dict[str, Any]], int]:
        """Decide whether to flush and, if so, snapshot the data under the cache lock.

        Returns (snapshot, count_in_snapshot). snapshot is None when no flush is due.

        We snapshot the write count so that after the disk write completes we can
        decrement by *exactly* what was persisted — writes that arrive during the
        flush remain unpersisted, and _dirty stays True for them.
        """
        now = time.time()
        if not self._dirty:
            return None, 0
        if (now - self._last_flush_ts) < self.flush_interval_sec and self._set_since_flush < self.flush_every_n:
            return None, 0

        items = []
        for k in self.lru_order:
            it = self.cache.get(k)
            if not it:
                continue
            slot = self._key_to_slot.get(k)
            if slot is None:
                # bookkeeping out of sync — skip rather than persist garbage
                continue
            items.append({
                "key": k,
                "val": {
                    "result": to_jsonable(it.get("result")),
                    "emb": self._emb[slot].tolist(),
                    "metadata": to_jsonable(it.get("metadata")),
                    "ts": it.get("ts"),
                }
            })

        snapshot_count = self._set_since_flush
        # Update last_flush_ts now so concurrent callers don't all queue
        # redundant flush attempts during the same flush_interval window.
        self._last_flush_ts = now
        return {"items": items}, snapshot_count

    def _flush_to_disk(self, data: Dict[str, Any], snapshot_count: int) -> None:
        """Write a snapshot to disk atomically. Runs OUTSIDE the cache lock.

        On success, decrements _set_since_flush by snapshot_count and clears
        _dirty if no writes arrived during the flush.
        """
        ok = False
        with self._flush_lock:
            try:
                self.persist_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = self.persist_path.with_suffix(self.persist_path.suffix + ".tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp_path, self.persist_path)
                ok = True
            except Exception as e:
                # Failure to persist should not stop the online service, but
                # log so corruption isn't silent.
                logger.warning("VectorizedLRUCache: flush to %s failed: %s", self.persist_path, e)

        if not ok:
            return

        with self.lock:
            self._set_since_flush = max(0, self._set_since_flush - snapshot_count)
            if self._set_since_flush == 0:
                self._dirty = False

    def _load_from_disk(self) -> None:
        if not self.persist_path.exists():
            return
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("VectorizedLRUCache: failed to read %s: %s", self.persist_path, e)
            return

        # Load back in order up to capacity
        for item in data.get("items", [])[: self.capacity]:
            try:
                k = item.get("key")
                val = item.get("val") or {}
                emb = val.get("emb") or None
                meta = val.get("metadata") or {}
                res = val.get("result")

                if not k or not emb:
                    continue
                if k in self._key_to_slot:
                    # Duplicate key in persisted data — skip to avoid leaking
                    # the old slot.
                    continue

                # Validate the embedding BEFORE popping a slot, otherwise a
                # shape mismatch would leak the slot (popped but never
                # assigned), eventually emptying _free_slots and forcing
                # writes onto slot 0 / corrupting existing entries.
                v = np.asarray(emb, dtype=np.float32)
                if v.ndim != 1 or v.shape[0] != self.dim:
                    continue

                if not self._free_slots:
                    break
                slot = self._free_slots.pop()

                self._key_to_slot[k] = slot
                self._slot_to_key[slot] = k
                self._emb[slot] = v

                self.cache[k] = {
                    "result": res,
                    "metadata": meta,
                    "ts": val.get("ts", time.time()),
                }
                self.lru_order.append(k)

                b = (meta or {}).get("bucket_id")
                if b is not None:
                    self._bucket_index.setdefault(b, set()).add(k)
            except Exception as e:
                logger.warning("VectorizedLRUCache: skipping malformed cache item: %s", e)
                continue