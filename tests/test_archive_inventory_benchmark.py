from scripts.benchmark_archive_inventory import measure


def test_archive_inventory_benchmark_compares_equal_work(tmp_path):
    legacy = measure("legacy", 10, 32)
    consolidated = measure("consolidated", 10, 32)
    assert legacy["tree_stats"] == consolidated["tree_stats"]
    assert legacy["symlink_stats"] == consolidated["symlink_stats"]
    assert legacy["traversal_count"] == 4
    assert consolidated["traversal_count"] == 2


def test_archive_inventory_cross_copy_benchmark_revalidates_both_trees():
    legacy = measure("legacy-cross", 10, 32)
    consolidated = measure("consolidated-cross", 10, 32)
    assert legacy["tree_stats"] == consolidated["tree_stats"]
    assert legacy["symlink_stats"] == consolidated["symlink_stats"]
    assert legacy["traversal_count"] == 8
    assert consolidated["traversal_count"] == 3
