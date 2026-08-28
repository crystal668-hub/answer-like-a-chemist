from __future__ import annotations

from scripts.patch_openclaw_minimax_ui import (
    DOLLAR_V_NEW,
    DOLLAR_V_OLD,
    LEGACY_HELPER,
    LEGACY_MODEL_REQUESTS,
    MODEL_REQUEST_OLD,
    PATCH_MARKER,
    QV_NEW,
    QV_OLD,
    REGISTRATION_VERSION_NEW,
    REGISTRATION_VERSION_OLD,
    SERVICE_WORKER_BUILD_ID,
    SERVICE_WORKER_PATCHED_BUILD_ID,
    patch_control_ui_bundle,
    patch_index_html,
    patch_service_worker,
)


def test_patch_updates_model_switch_and_thinking_picker() -> None:
    source = f"{QV_OLD}{DOLLAR_V_OLD}{MODEL_REQUEST_OLD}{REGISTRATION_VERSION_OLD}"

    patched, changed = patch_control_ui_bundle(source)

    assert changed is True
    assert PATCH_MARKER in patched
    assert "id:`adaptive`,label:`adaptive`" in patched
    assert "l=isMiniMaxM3ModelRef(a,o)?`adaptive`" in patched
    assert "isMiniMaxM3ModelRef(t||UB(e))?{thinkingLevel:`adaptive`}:" in patched
    assert "isMiniMaxM3ModelRef(GV(e).provider,GV(e).model)?{thinkingLevel:null}:{}" in patched
    assert REGISTRATION_VERSION_NEW in patched


def test_patch_is_idempotent() -> None:
    source = f"{QV_OLD}{DOLLAR_V_OLD}{MODEL_REQUEST_OLD}"
    patched, changed = patch_control_ui_bundle(source)

    same, changed_again = patch_control_ui_bundle(patched)

    assert changed is True
    assert changed_again is False
    assert same == patched


def test_patch_upgrades_the_initial_local_patch_revision() -> None:
    legacy_request = LEGACY_MODEL_REQUESTS[-1]
    source = f"{LEGACY_HELPER}{QV_NEW}{DOLLAR_V_NEW}{legacy_request}"

    patched, changed = patch_control_ui_bundle(source)

    assert changed is True
    assert "isMiniMaxM3ModelRef(GV(e).provider,GV(e).model)?{thinkingLevel:null}:{} )}" in patched


def test_service_worker_cache_namespace_is_bumped(tmp_path) -> None:
    worker = tmp_path / "sw.js"
    worker.write_text(
        f'const EMBEDDED_CACHE_VERSION = "{SERVICE_WORKER_BUILD_ID}";\n',
        encoding="utf-8",
    )

    assert patch_service_worker(worker) is True
    assert SERVICE_WORKER_PATCHED_BUILD_ID in worker.read_text(encoding="utf-8")
    assert patch_service_worker(worker) is False


def test_index_html_cache_busts_the_main_bundle(tmp_path) -> None:
    index = tmp_path / "index.html"
    index.write_text(
        '<script type="module" src="./assets/index.js"></script>\n',
        encoding="utf-8",
    )

    assert patch_index_html(index, "index.js") is True
    assert f"./assets/index.js?v={SERVICE_WORKER_PATCHED_BUILD_ID}" in index.read_text(
        encoding="utf-8"
    )
    assert patch_index_html(index, "index.js") is False
