import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

class DistributedCacheManager:
    """
    Distributed Cache Manager for multi-agent processes.
    Supports Redis, falls back to local memory if Redis is unavailable.
    """

    def __init__(self, redis_url: str = None):
        self.redis = None
        self._local_cache = {}

        if redis_url:
            try:
                import redis
                self.redis = redis.from_url(redis_url, decode_responses=True)
                # Test connection
                self.redis.ping()
                logger.info("Connected to Redis cache.")
            except ImportError:
                logger.warning("redis-py not installed. Falling back to local cache.")
                self.redis = None
            except Exception:
                logger.warning("Redis connection failed; falling back to local cache.")
                self.redis = None

    def get(self, key: str) -> Optional[Any]:
        if self.redis:
            try:
                val = self.redis.get(key)
                if val:
                    return json.loads(val)
            except Exception:
                logger.debug("Redis get failed; falling back to local cache.")

        # Fallback to local cache
        if key in self._local_cache:
            value, expiry = self._local_cache[key]
            if time.time() < expiry:
                return value
            else:
                del self._local_cache[key]
        return None

    def set(self, key: str, value: Any, ttl_seconds: int = 3600):
        if self.redis:
            try:
                self.redis.setex(key, ttl_seconds, json.dumps(value))
                return
            except Exception:
                logger.debug("Redis set failed; falling back to local cache.")

        # Fallback to local cache
        expiry = time.time() + ttl_seconds
        self._local_cache[key] = (value, expiry)
