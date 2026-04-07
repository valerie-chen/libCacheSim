from collections import OrderedDict
from libcachesim import CommonCacheParams, Request


class LruCache:
    """
    LRU: evict the least recently used object.
    Uses OrderedDict for O(1) access, insertion, and move-to-end.
    """

    def __init__(self, cache_size: int):
        self.cache_size = cache_size
        # OrderedDict: first = LRU (eviction candidate), last = MRU
        self.cache = OrderedDict()  # obj_id -> obj_size

    def on_hit(self, req: Request):
        self.cache.move_to_end(req.obj_id)

    def on_miss(self, req: Request):
        self.cache[req.obj_id] = req.obj_size

    def evict(self, req: Request) -> int:
        if not self.cache:
            return 0
        obj_id, _ = self.cache.popitem(last=False)
        return obj_id

    def on_remove(self, obj_id: int):
        self.cache.pop(obj_id, None)


def cache_init_hook(common_cache_params: CommonCacheParams):
    return LruCache(common_cache_params.cache_size)


def cache_hit_hook(data: LruCache, req: Request):
    data.on_hit(req)


def cache_miss_hook(data: LruCache, req: Request):
    data.on_miss(req)


def cache_eviction_hook(data: LruCache, req: Request) -> int:
    return data.evict(req)


def cache_remove_hook(data: LruCache, obj_id: int):
    data.on_remove(obj_id)


def cache_free_hook(data: LruCache):
    data.cache.clear()


if __name__ == "__main__":
    from pathlib import Path
    from libcachesim import PluginCache, TraceReader, TraceType

    lru_cache = PluginCache(
        cache_size=1024 * 1024,  # 1 MB
        cache_init_hook=cache_init_hook,
        cache_hit_hook=cache_hit_hook,
        cache_miss_hook=cache_miss_hook,
        cache_eviction_hook=cache_eviction_hook,
        cache_remove_hook=cache_remove_hook,
        cache_free_hook=cache_free_hook,
        cache_name="lru",
    )

    trace = Path(__file__).parent.parent / "data" / "cloudPhysicsIO.vscsi"
    reader = TraceReader(trace=str(trace), trace_type=TraceType.VSCSI_TRACE)

    req_miss_ratio, byte_miss_ratio = lru_cache.process_trace(reader)
    print(f"Request miss ratio: {req_miss_ratio:.4f}")
    print(f"Byte miss ratio: {byte_miss_ratio:.4f}")
