"""LRU eviction — least *recently used*, not least recently inserted.

The current implementation evicts in insertion order, so reading a key does
not protect it. The obvious repair (move the key on put) fixes eviction after
writes and leaves reads broken; both tests below must pass together.
"""

import pytest

from cache import LRUCache


def test_respects_capacity():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("c", 3)
    assert len(c) == 2


def test_rejects_bad_capacity():
    with pytest.raises(ValueError):
        LRUCache(0)


def test_a_read_protects_a_key_from_eviction():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")            # "a" is now the most recently used
    c.put("c", 3)         # evicts "b", not "a"
    assert c.get("a") == 1
    assert c.get("b") is None


def test_overwriting_a_key_refreshes_it():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("a", 10)        # "a" refreshed, "b" is now oldest
    c.put("c", 3)
    assert c.get("a") == 10
    assert c.get("b") is None


def test_overwrite_does_not_grow_the_cache():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("a", 2)
    assert len(c) == 1
