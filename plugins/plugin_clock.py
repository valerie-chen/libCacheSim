from collections import deque
from libcachesim import CommonCacheParams, Request

# Number of frequency bits (1 = second chance, 2 = up to 3 uses before eviction)
N_BIT_COUNTER = 2


class ClockCache:
    """
    Clock (multi-bit): a circular FIFO with frequency-based second-chance eviction.

    Objects are inserted at the head with freq=init_freq. On access, freq is
    incremented up to max_freq = 2^N_BIT_COUNTER - 1. On eviction, the clock
    hand scans from the tail; objects with freq>0 are decremented and given a
    second chance (moved to the head), and the first object with freq=0 is
    evicted.

    With N_BIT_COUNTER=1 this is the classic Clock / FIFO-with-second-chance.
    With N_BIT_COUNTER=2 objects survive up to 3 accesses before being evicted.
    """

    def __init__(self, cache_size: int, n_bits: int = N_BIT_COUNTER, init_freq: int = 0):
        self.cache_size = cache_size
        self.max_freq = (1 << n_bits) - 1  # e.g. 1 for 1-bit, 3 for 2-bit
        self.init_freq = init_freq
        # appendleft = newest (head), pop = eviction candidate (tail)
        self.queue = deque()
        self.freq = {}      # obj_id -> current frequency
        self.in_cache = set()  # for O(1) membership; handles stale deque entries

    def on_hit(self, req: Request):
        obj_id = req.obj_id
        if obj_id in self.freq and self.freq[obj_id] < self.max_freq:
            self.freq[obj_id] += 1

    def on_miss(self, req: Request):
        obj_id = req.obj_id
        self.queue.appendleft(obj_id)
        self.freq[obj_id] = self.init_freq
        self.in_cache.add(obj_id)

    def evict(self, req: Request) -> int:
        while self.queue:
            obj_id = self.queue.pop()

            if obj_id not in self.in_cache:
                # Stale entry from a previous explicit remove
                continue

            if self.freq.get(obj_id, 0) >= 1:
                # Give second chance: decrement freq and move to head
                self.freq[obj_id] -= 1
                self.queue.appendleft(obj_id)
                continue

            # freq == 0: evict this object
            self.in_cache.discard(obj_id)
            del self.freq[obj_id]
            return obj_id

        return 0

    def on_remove(self, obj_id: int):
        # Mark as not in cache; stale deque entries cleaned up lazily in evict()
        self.in_cache.discard(obj_id)
        self.freq.pop(obj_id, None)


def cache_init_hook(common_cache_params: CommonCacheParams):
    return ClockCache(common_cache_params.cache_size)


def cache_hit_hook(data: ClockCache, req: Request):
    data.on_hit(req)


def cache_miss_hook(data: ClockCache, req: Request):
    data.on_miss(req)


def cache_eviction_hook(data: ClockCache, req: Request) -> int:
    return data.evict(req)


def cache_remove_hook(data: ClockCache, obj_id: int):
    data.on_remove(obj_id)


def cache_free_hook(data: ClockCache):
    data.queue.clear()
    data.freq.clear()
    data.in_cache.clear()


if __name__ == "__main__":
    from pathlib import Path
    from libcachesim import PluginCache, TraceReader, TraceType

    clock_cache = PluginCache(
        cache_size=1024 * 1024,  # 1 MB
        cache_init_hook=cache_init_hook,
        cache_hit_hook=cache_hit_hook,
        cache_miss_hook=cache_miss_hook,
        cache_eviction_hook=cache_eviction_hook,
        cache_remove_hook=cache_remove_hook,
        cache_free_hook=cache_free_hook,
        cache_name="clock",
    )

    trace = Path(__file__).parent.parent / "data" / "cloudPhysicsIO.vscsi"
    reader = TraceReader(trace=str(trace), trace_type=TraceType.VSCSI_TRACE)

    req_miss_ratio, byte_miss_ratio = clock_cache.process_trace(reader)
    print(f"Request miss ratio: {req_miss_ratio:.4f}")
    print(f"Byte miss ratio: {byte_miss_ratio:.4f}")
