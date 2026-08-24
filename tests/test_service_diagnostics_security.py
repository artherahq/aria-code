from aria_code.packages.aria_services.cache import DistributedCacheManager
from aria_code.packages.aria_services.provider_health import classify_provider_error


def test_unclassified_provider_errors_do_not_retain_sensitive_text():
    issue = classify_provider_error(
        "fixture", "request failed for https://example.test/?token=not-for-logs"
    )

    assert issue.category == "error"
    assert issue.message == "provider request failed"
    assert "not-for-logs" not in issue.message


def test_cache_manager_falls_back_to_local_memory_without_a_redis_url():
    cache = DistributedCacheManager()

    cache.set("fixture", {"value": 1}, ttl_seconds=10)

    assert cache.get("fixture") == {"value": 1}
