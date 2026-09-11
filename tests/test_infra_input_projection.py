from pathlib import Path

from benchmarking.runtime.bundles import RuntimeBundle, RuntimePathProjection


def test_bundle_projection_keeps_host_evidence_and_projects_all_assets(tmp_path):
    bundle = tmp_path / "input"
    (bundle / "images").mkdir(parents=True)
    (bundle / "question.md").write_text("![input](images/a.png)")
    (bundle / "images/a.png").write_bytes(b"image")
    original = RuntimeBundle(bundle, bundle / "question.md", [bundle / "images/a.png"])
    projection = RuntimePathProjection(
        tmp_path / "workspace", tmp_path / "skills", original
    )
    visible = projection.visible_bundle()
    assert visible.question_markdown == Path("/benchmark/input/question.md")
    assert visible.image_files == [Path("/benchmark/input/images/a.png")]
    assert original.bundle_dir == bundle
    assert projection.audit_mappings()["/benchmark/input"] == str(bundle)


def test_projection_without_bundle_has_no_input_mapping(tmp_path):
    projection = RuntimePathProjection(tmp_path / "workspace", tmp_path / "skills")
    assert projection.visible_bundle() is None
    assert "/benchmark/input" not in projection.audit_mappings()
