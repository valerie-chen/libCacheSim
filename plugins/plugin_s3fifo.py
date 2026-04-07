from collections import deque
from libcachesim import CommonCacheParams, Request

# Default S3-FIFO parameters
SMALL_SIZE_RATIO = 0.10       # small queue = 10% of cache
GHOST_SIZE_RATIO = 0.90       # ghost queue = 90% of cache size
MOVE_TO_MAIN_THRESHOLD = 2    # promote from small → main when freq >= this


class S3FifoCache:
    """
    S3-FIFO: Simple, Scalable eviction algorithm with three FIFO queues.

    Structure:
      - small queue  (10% of cache): new objects enter here with freq=0
      - main queue   (90% of cache): promoted objects, evicted with 2-bit Clock
      - ghost queue  (~90% of cache, not counted against cache size):
            tracks recently evicted obj_ids from small

    Insertion:
      - If obj_id is in ghost → insert into main (bypass small)
      - If obj_size >= small_size → insert into main (too large)
      - If small not yet used as FIFO (pre-eviction warmup, small full) → main
      - Otherwise → insert into small with freq=0

    Eviction from small:
      - Scan from tail; if freq >= threshold → promote to main (freq reset to 0)
      - First object with freq < threshold → evict, add obj_id to ghost

    Eviction from main (2-bit Clock):
      - Scan from tail; if freq >= 1 → decrement freq, reinsert at head
      - First object with freq == 0 → evict

    Trigger: evict from main when main_used > main_size or small is empty,
             otherwise evict from small.
    """

    def __init__(self, cache_size: int):
        self.cache_size = cache_size
        self.small_size = max(1, int(cache_size * SMALL_SIZE_RATIO))
        self.main_size = cache_size - self.small_size
        self.ghost_size = int(cache_size * GHOST_SIZE_RATIO)

        # FIFO queues: appendleft = newest (head), pop = oldest (tail)
        self.small_q = deque()
        self.main_q = deque()
        self.ghost_q = deque()  # stores (obj_id, obj_size) tuples

        # Membership sets / dicts
        self.in_small = set()
        self.in_main = set()
        self.in_ghost = {}   # obj_id -> obj_size (acts as ghost set + size lookup)

        # Per-object metadata for objects currently in small or main
        self.freq = {}   # obj_id -> frequency count
        self.size = {}   # obj_id -> obj_size

        # Occupied byte counts
        self.small_used = 0
        self.main_used = 0
        self.ghost_used = 0

        # True after the first eviction; changes small-queue overflow behavior
        self.has_evicted = False

    # ------------------------------------------------------------------
    # Plugin hooks
    # ------------------------------------------------------------------

    def on_hit(self, req: Request):
        obj_id = req.obj_id
        if obj_id in self.freq:
            # Increment frequency, capped at 3 (2-bit counter)
            self.freq[obj_id] = min(self.freq[obj_id] + 1, 3)

    def on_miss(self, req: Request):
        obj_id = req.obj_id
        obj_size = req.obj_size

        if obj_id in self.in_ghost:
            # Ghost hit: promote directly into main
            ghost_sz = self.in_ghost.pop(obj_id)
            self.ghost_used -= ghost_sz
            self._insert_main(obj_id, obj_size)
        elif obj_size >= self.small_size:
            # Object too large for small queue
            self._insert_main(obj_id, obj_size)
        elif not self.has_evicted and self.small_used >= self.small_size:
            # Warmup phase: small is full but cache hasn't evicted yet → overflow to main
            self._insert_main(obj_id, obj_size)
        else:
            self._insert_small(obj_id, obj_size)

    def evict(self, req: Request) -> int:
        self.has_evicted = True
        # Evict from main if it's overfull or small is empty
        if self.main_used > self.main_size or self.small_used == 0:
            return self._evict_main()
        return self._evict_small()

    def on_remove(self, obj_id: int):
        """Called by the framework after eviction or for explicit removals."""
        if obj_id in self.in_small:
            self.in_small.discard(obj_id)
            self.small_used -= self.size.get(obj_id, 0)
        elif obj_id in self.in_main:
            self.in_main.discard(obj_id)
            self.main_used -= self.size.get(obj_id, 0)
        # Ghost is managed independently; don't touch it here
        self.freq.pop(obj_id, None)
        self.size.pop(obj_id, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _insert_small(self, obj_id: int, obj_size: int):
        self.small_q.appendleft(obj_id)
        self.in_small.add(obj_id)
        self.freq[obj_id] = 0
        self.size[obj_id] = obj_size
        self.small_used += obj_size

    def _insert_main(self, obj_id: int, obj_size: int):
        self.main_q.appendleft(obj_id)
        self.in_main.add(obj_id)
        self.freq[obj_id] = 0
        self.size[obj_id] = obj_size
        self.main_used += obj_size

    def _add_to_ghost(self, obj_id: int, obj_size: int):
        # Evict old ghost entries until there is room
        while self.ghost_used + obj_size > self.ghost_size and self.ghost_q:
            old_id, old_size = self.ghost_q.pop()
            if old_id in self.in_ghost:
                del self.in_ghost[old_id]
                self.ghost_used -= old_size
        self.ghost_q.appendleft((obj_id, obj_size))
        self.in_ghost[obj_id] = obj_size
        self.ghost_used += obj_size

    def _evict_small(self) -> int:
        """
        Scan small queue from tail.
        - freq >= threshold → promote to main (object stays in cache)
        - freq <  threshold → evict, add to ghost, return obj_id
        Falls back to _evict_main if small becomes empty.
        """
        while self.small_q:
            obj_id = self.small_q.pop()

            if obj_id not in self.in_small:
                continue  # stale entry from an earlier explicit remove

            freq = self.freq.get(obj_id, 0)
            obj_size = self.size.get(obj_id, 1)

            self.in_small.discard(obj_id)
            self.small_used -= obj_size

            if freq >= MOVE_TO_MAIN_THRESHOLD:
                # Promote to main; freq resets to 0 (fresh entry in main)
                self.main_q.appendleft(obj_id)
                self.in_main.add(obj_id)
                self.freq[obj_id] = 0
                self.main_used += obj_size
                # Keep scanning small for something to actually evict
            else:
                # Evict from cache; track in ghost for future re-insertions
                del self.freq[obj_id]
                del self.size[obj_id]
                self._add_to_ghost(obj_id, obj_size)
                return obj_id

        # Small is empty (everything was promoted); fall through to main
        return self._evict_main()

    def _evict_main(self) -> int:
        """
        Scan main queue from tail using a 2-bit Clock.
        - freq >= 1 → decrement freq, reinsert at head
        - freq == 0 → evict, return obj_id
        """
        while self.main_q:
            obj_id = self.main_q.pop()

            if obj_id not in self.in_main:
                continue  # stale entry

            freq = self.freq.get(obj_id, 0)
            obj_size = self.size.get(obj_id, 1)

            self.in_main.discard(obj_id)
            self.main_used -= obj_size

            if freq >= 1:
                # Decrement and reinsert at head (2-bit Clock)
                self.main_q.appendleft(obj_id)
                self.in_main.add(obj_id)
                self.freq[obj_id] = min(freq, 3) - 1
                self.main_used += obj_size
                # Keep scanning
            else:
                # freq == 0: evict
                del self.freq[obj_id]
                del self.size[obj_id]
                return obj_id

        return 0  # should not be reached if cache is non-empty


def cache_init_hook(common_cache_params: CommonCacheParams):
    return S3FifoCache(common_cache_params.cache_size)


def cache_hit_hook(data: S3FifoCache, req: Request):
    data.on_hit(req)


def cache_miss_hook(data: S3FifoCache, req: Request):
    data.on_miss(req)


def cache_eviction_hook(data: S3FifoCache, req: Request) -> int:
    return data.evict(req)


def cache_remove_hook(data: S3FifoCache, obj_id: int):
    data.on_remove(obj_id)


def cache_free_hook(data: S3FifoCache):
    data.small_q.clear()
    data.main_q.clear()
    data.ghost_q.clear()
    data.in_small.clear()
    data.in_main.clear()
    data.in_ghost.clear()
    data.freq.clear()
    data.size.clear()


if __name__ == "__main__":
    from pathlib import Path
    from libcachesim import PluginCache, TraceReader, TraceType

    s3fifo_cache = PluginCache(
        cache_size=1024 * 1024,  # 1 MB
        cache_init_hook=cache_init_hook,
        cache_hit_hook=cache_hit_hook,
        cache_miss_hook=cache_miss_hook,
        cache_eviction_hook=cache_eviction_hook,
        cache_remove_hook=cache_remove_hook,
        cache_free_hook=cache_free_hook,
        cache_name="s3fifo",
    )

    trace = Path(__file__).parent.parent / "data" / "cloudPhysicsIO.vscsi"
    reader = TraceReader(trace=str(trace), trace_type=TraceType.VSCSI_TRACE)

    req_miss_ratio, byte_miss_ratio = s3fifo_cache.process_trace(reader)
    print(f"Request miss ratio: {req_miss_ratio:.4f}")
    print(f"Byte miss ratio: {byte_miss_ratio:.4f}")
