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
    patch_control_ui_bundle,
)


def test_patch_updates_model_switch_and_thinking_picker() -> None:
    source = f"{QV_OLD}{DOLLAR_V_OLD}{MODEL_REQUEST_OLD}"

    patched, changed = patch_control_ui_bundle(source)

    assert changed is True
    assert PATCH_MARKER in patched
    assert "id:`adaptive`,label:`adaptive`" in patched
    assert "l=isMiniMaxM3ModelRef(a,o)?`adaptive`" in patched
    assert "isMiniMaxM3ModelRef(t||UB(e))?{thinkingLevel:`adaptive`}:" in patched
    assert "isMiniMaxM3ModelRef(GV(e).provider,GV(e).model)?{thinkingLevel:null}:{}" in patched


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
