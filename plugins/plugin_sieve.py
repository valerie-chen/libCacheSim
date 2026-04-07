from libcachesim import CommonCacheParams, Request


class Node:
    """Doubly-linked list node for the Sieve cache queue."""
    __slots__ = ["obj_id", "freq", "prev", "next"]

    def __init__(self, obj_id: int):
        self.obj_id = obj_id
        self.freq = 0
        self.prev = None   # toward head (more recently inserted)
        self.next = None   # toward tail (less recently inserted)


class SieveCache:
    """
    Sieve: a simple, efficient eviction algorithm.

    Objects are inserted at the head with freq=0. On access, freq is set to 1.
    On eviction, a pointer scans from tail toward head. Objects with freq=1
    are decremented to 0 (given a second chance). The first object encountered
    with freq=0 is evicted and the pointer advances past it.

    Key properties:
    - No object movement on access (unlike LRU) — cache-friendly
    - One-bit frequency counter per object
    - Pointer position preserves scan state across evictions
    """

    def __init__(self, cache_size: int):
        self.cache_size = cache_size
        self.head = None     # most recently inserted
        self.tail = None     # least recently inserted
        self.pointer = None  # scanning position; None means start from tail
        self.node_map = {}   # obj_id -> Node for O(1) lookup

    def on_hit(self, req: Request):
        node = self.node_map.get(req.obj_id)
        if node is not None:
            node.freq = 1

    def on_miss(self, req: Request):
        node = Node(req.obj_id)
        self.node_map[req.obj_id] = node
        # Insert at head (most recently inserted)
        node.next = self.head
        node.prev = None
        if self.head is not None:
            self.head.prev = node
        self.head = node
        if self.tail is None:
            self.tail = node

    def evict(self, req: Request) -> int:
        # Start from pointer; if None, start from tail (oldest)
        obj = self.pointer if self.pointer is not None else self.tail
        if obj is None:
            return 0

        # Scan: decrement freq>0 and skip; stop at first freq==0
        while obj.freq > 0:
            obj.freq -= 1
            # Advance toward head; wrap to tail when past head
            obj = obj.prev if obj.prev is not None else self.tail

        # obj.freq == 0: evict this object
        self.pointer = obj.prev  # advance pointer (may be None if obj was head)
        obj_id = obj.obj_id
        self._remove_node(obj)
        return obj_id

    def _remove_node(self, node: Node):
        # Unlink from doubly-linked list
        if node.prev is not None:
            node.prev.next = node.next
        else:
            self.head = node.next  # node was head

        if node.next is not None:
            node.next.prev = node.prev
        else:
            self.tail = node.prev  # node was tail

        self.node_map.pop(node.obj_id, None)

    def on_remove(self, obj_id: int):
        node = self.node_map.get(obj_id)
        if node is None:
            return
        # Advance pointer if it points to the removed node
        if self.pointer is node:
            self.pointer = node.prev
        self._remove_node(node)


def cache_init_hook(common_cache_params: CommonCacheParams):
    return SieveCache(common_cache_params.cache_size)


def cache_hit_hook(data: SieveCache, req: Request):
    data.on_hit(req)


def cache_miss_hook(data: SieveCache, req: Request):
    data.on_miss(req)


def cache_eviction_hook(data: SieveCache, req: Request) -> int:
    return data.evict(req)


def cache_remove_hook(data: SieveCache, obj_id: int):
    data.on_remove(obj_id)


def cache_free_hook(data: SieveCache):
    data.head = None
    data.tail = None
    data.pointer = None
    data.node_map.clear()


if __name__ == "__main__":
    from pathlib import Path
    from libcachesim import PluginCache, TraceReader, TraceType

    sieve_cache = PluginCache(
        cache_size=1024 * 1024,  # 1 MB
        cache_init_hook=cache_init_hook,
        cache_hit_hook=cache_hit_hook,
        cache_miss_hook=cache_miss_hook,
        cache_eviction_hook=cache_eviction_hook,
        cache_remove_hook=cache_remove_hook,
        cache_free_hook=cache_free_hook,
        cache_name="sieve",
    )

    trace = Path(__file__).parent.parent / "data" / "cloudPhysicsIO.vscsi"
    reader = TraceReader(trace=str(trace), trace_type=TraceType.VSCSI_TRACE)

    req_miss_ratio, byte_miss_ratio = sieve_cache.process_trace(reader)
    print(f"Request miss ratio: {req_miss_ratio:.4f}")
    print(f"Byte miss ratio: {byte_miss_ratio:.4f}")
