import json

from scripts import benchmark_vgb_validation_cache


def test_validation_cache_benchmark_uses_real_pinned_files():
    uncached = benchmark_vgb_validation_cache.measure("uncached", 2)
    cached = benchmark_vgb_validation_cache.measure("cached", 2)
    assert json.dumps(uncached["manifest"], sort_keys=True) == json.dumps(cached["manifest"], sort_keys=True)
    assert cached["cache"] == {"hit_count": 1, "miss_count": 1, "failure_count": 0}
