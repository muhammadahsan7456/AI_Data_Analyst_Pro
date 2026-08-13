import time
import threading
from collections import OrderedDict


class LRUTTLCache:
    """
    Thread-Safe High-Performance In-Memory LRU Cache with Time-To-Live (TTL) expiration.
    Designed to accelerate dashboard metrics, AI queries, dataset profiling, and chart lookups.
    """

    def __init__(self, maxsize: int = 1000, default_ttl: int = 300):
        self.maxsize = maxsize
        self.default_ttl = default_ttl
        self.cache = OrderedDict()
        self.lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        with self.lock:
            if key not in self.cache:
                self.misses += 1
                return None

            value, expires_at = self.cache[key]
            if time.time() > expires_at:
                del self.cache[key]
                self.misses += 1
                return None

            # Move to end (most recently used)
            self.cache.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value, ttl: int = None):
        ttl = ttl if ttl is not None else self.default_ttl
        expires_at = time.time() + ttl

        with self.lock:
            if key in self.cache:
                del self.cache[key]

            self.cache[key] = (value, expires_at)
            self.cache.move_to_end(key)

            # Evict oldest item if over maxsize
            if len(self.cache) > self.maxsize:
                self.cache.popitem(last=False)

    def delete(self, key: str):
        with self.lock:
            if key in self.cache:
                del self.cache[key]

    def invalidate_pattern(self, pattern: str):
        """
        Invalidate all keys containing a specific substring (e.g. user_id or table_name).
        """
        with self.lock:
            keys_to_del = [k for k in self.cache if pattern in k]
            for k in keys_to_del:
                del self.cache[k]

    def clear(self):
        with self.lock:
            self.cache.clear()
            self.hits = 0
            self.misses = 0

    def get_stats(self) -> dict:
        with self.lock:
            total = self.hits + self.misses
            hit_ratio = round((self.hits / total * 100), 1) if total > 0 else 100.0
            return {
                "size": len(self.cache),
                "maxsize": self.maxsize,
                "hits": self.hits,
                "misses": self.misses,
                "hit_ratio": hit_ratio
            }


# Singleton system cache instance
system_cache = LRUTTLCache(maxsize=2000, default_ttl=600)
