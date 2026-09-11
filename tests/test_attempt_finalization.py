import json

from benchmarking.runtime.attempt_finalization import cleanup_owned_environment, read_evidence, write_evidence


def test_truncated_manifest_is_unavailable_and_original_bytes_are_preserved(tmp_path):
    path = tmp_path / "dependency-manifest.json"
    path.write_text('{"identity":')
    assert read_evidence(path)["status"] == "unavailable"
    assert path.read_text() == '{"identity":'


def test_cleanup_does_not_follow_cache_parent_symlink(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    outside = tmp_path / "outside"
    (outside / "cache/uv").mkdir(parents=True)
    marker = outside / "cache/uv/keep"
    marker.write_text("evidence")
    (scratch / "tmp").symlink_to(outside, target_is_directory=True)
    report = cleanup_owned_environment(scratch)
    assert report["status"] == "failed"
    assert marker.read_text() == "evidence"


def test_atomic_evidence_write_leaves_no_temporary_file(tmp_path):
    target = tmp_path / "evidence.json"
    write_evidence(target, {"status": "partial"})
    assert json.loads(target.read_text()) == {"status": "partial"}
    assert list(tmp_path.iterdir()) == [target]
