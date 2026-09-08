"""A tiny LRU cache."""


class LRUCache:
    def __init__(self, capacity):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._data = {}

    def get(self, key, default=None):
        if key not in self._data:
            return default
        return self._data[key]

    def put(self, key, value):
        self._data[key] = value
        if len(self._data) > self.capacity:
            oldest = next(iter(self._data))
            del self._data[oldest]

    def __len__(self):
        return len(self._data)
