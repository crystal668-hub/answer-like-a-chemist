#!/usr/bin/env python3
"""Patch the installed OpenClaw Control UI for MiniMax-M3 thinking levels.

OpenClaw 2026.6.9 ships the Control UI as a prebuilt JavaScript bundle.  The
runtime already advertises MiniMax-M3's ``off``/``adaptive`` profile, but the
bundle can retain a previous ``high`` session override when the model changes.
This small, version-pinned patch keeps the UI and the session patch in sync.
"""

from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_BUNDLE = Path(
    "/Users/xutao/.nvm/versions/node/v24.15.0/lib/node_modules/openclaw"
    "/dist/control-ui/assets/index-ogWrBZIb.js"
)
PATCH_MARKER = "function isMiniMaxM3ModelRef"
PATCH_COMPLETE_MARKER = "isMiniMaxM3ModelRef(GV(e).provider,GV(e).model)?{thinkingLevel:null}:{}"

HELPER = (
    "function isMiniMaxM3ModelRef(e,t){let n=typeof e==`string`?e.trim().toLowerCase():``,"
    "r=typeof t==`string`?t.trim():``;if(r.includes(`/`)){let e=r.indexOf(`/`);n=r.slice(0,e).toLowerCase(),"
    "r=r.slice(e+1)}else if(!r){let e=n.indexOf(`/`);if(e>0)r=n.slice(e+1),n=n.slice(0,e).toLowerCase();"
    "else if(/^minimax-m3(?:\\b|[-.])/i.test(n))r=n,n=`minimax`}return(n===`minimax`||n===`minimax-portal`)"
    "&&/^MiniMax-M3(?:\\b|[-.])/i.test(r)}"
)
LEGACY_HELPER = (
    "function isMiniMaxM3ModelRef(e,t){let n=typeof e==`string`?e.trim().toLowerCase():``,"
    "r=typeof t==`string`?t.trim():``;if(!r){let i=n.indexOf(`/`);if(i<=0)return!1;"
    "r=n.slice(i+1),n=n.slice(0,i).toLowerCase()}return(n===`minimax`||n===`minimax-portal`)"
    "&&/^MiniMax-M3(?:\\b|[-.])/i.test(r)}"
)

QV_OLD = (
    "function QV(e,t,n,r,i){let a=(!e?.modelProvider||e.modelProvider===t?.modelProvider)"
    "&&(!e?.model||e.model===t?.model),o=n&&r?i.find(e=>e.provider===n&&e.id===r):void 0,"
    "s=(e?.thinkingLevels?.length?e.thinkingLevels:null)??(a&&t?.thinkingLevels?.length?"
    "t.thinkingLevels:null);if(s)return o?.reasoning===!1&&ZV(s)?[]:s;let c=(e?.thinkingOptions?."
    "length?e.thinkingOptions:null)??(a&&t?.thinkingOptions?.length?t.thinkingOptions:null);"
    "return o?.reasoning===!1&&(!c||c.every(XV))?[]:(c??(n&&r?lp(n,r):lp())).map(e=>({"
    "id:cp(e)??w(e),label:e}))}"
)
QV_NEW = (
    "function QV(e,t,n,r,i){let a=(!e?.modelProvider||e.modelProvider===t?.modelProvider)"
    "&&(!e?.model||e.model===t?.model),o=n&&r?i.find(e=>e.provider===n&&e.id===r):void 0,"
    "s=(e?.thinkingLevels?.length?e.thinkingLevels:null)??(a&&t?.thinkingLevels?.length?"
    "t.thinkingLevels:null);if(isMiniMaxM3ModelRef(n,r))return[{id:`off`,label:`off`},{"
    "id:`adaptive`,label:`adaptive`}];if(s)return o?.reasoning===!1&&ZV(s)?[]:s;let c=("
    "e?.thinkingOptions?.length?e.thinkingOptions:null)??(a&&t?.thinkingOptions?.length?"
    "t.thinkingOptions:null);return o?.reasoning===!1&&(!c||c.every(XV))?[]:(c??(n&&r?"
    "lp(n,r):lp())).map(e=>({id:cp(e)??w(e),label:e}))}"
)

DOLLAR_V_OLD = (
    "function $V(e){let t=e.sessionsResult?.sessions?.find(t=>t.key===e.sessionKey),"
    "n=t?.thinkingLevel,r=typeof n==`string`&&n.trim()?cp(n)??n.trim():``,i=e.sessionsResult?."
    "defaults,{provider:a,model:o}=GV(e),s=QV(t,i,a,o,e.chatModelCatalog??[]),c=(!t||JV(t,i))"
    "&&i?.thinkingDefault?i.thinkingDefault:void 0,l=t?.thinkingDefault??c??(a&&o?dp({provider:a,"
    "model:o,catalog:e.chatModelCatalog??[]}):`off`),u=s.length===0&&r===`off`?``:r;return{"
    "currentOverride:u,defaultLabel:nV(l),options:YV(s,u)}}"
)
DOLLAR_V_NEW = (
    "function $V(e){let t=e.sessionsResult?.sessions?.find(t=>t.key===e.sessionKey),"
    "n=t?.thinkingLevel,r=typeof n==`string`&&n.trim()?cp(n)??n.trim():``,i=e.sessionsResult?."
    "defaults,{provider:a,model:o}=GV(e),s=QV(t,i,a,o,e.chatModelCatalog??[]),c=(!t||JV(t,i))"
    "&&i?.thinkingDefault?i.thinkingDefault:void 0,l=isMiniMaxM3ModelRef(a,o)?`adaptive`:"
    "t?.thinkingDefault??c??(a&&o?dp({provider:a,model:o,catalog:e.chatModelCatalog??[]}):`off`),"
    "u=isMiniMaxM3ModelRef(a,o)&&r!==`off`&&r!==`adaptive`?``:s.length===0&&r===`off`?``:r;"
    "return{currentOverride:u,defaultLabel:nV(l),options:YV(s,u)}}"
)

MODEL_REQUEST_OLD = "model:t||null}),UV(e),await dV(e),!0"
MODEL_REQUEST_NEW = (
    "model:t||null,...(isMiniMaxM3ModelRef(t||UB(e))?{thinkingLevel:`adaptive`}:"
    "isMiniMaxM3ModelRef(GV(e).provider,GV(e).model)?{thinkingLevel:null}:{} )}),UV(e),"
    "await dV(e),!0"
)
LEGACY_MODEL_REQUESTS = (
    "model:t||null,...isMiniMaxM3ModelRef(t||UB(e))?{thinkingLevel:`adaptive`}:{}),UV(e),await dV(e),!0",
    "model:t||null,...(isMiniMaxM3ModelRef(t||UB(e))?{thinkingLevel:`adaptive`}:{} )}),UV(e),await dV(e),!0",
    "model:t||null,...(isMiniMaxM3ModelRef(t||UB(e))?{thinkingLevel:`adaptive`}:isMiniMaxM3ModelRef(GV(e).provider,GV(e).model)?{thinkingLevel:null}:{})),UV(e),await dV(e),!0",
)


def patch_control_ui_bundle(source: str) -> tuple[str, bool]:
    """Return a patched bundle and whether any replacement was made."""

    if PATCH_COMPLETE_MARKER in source:
        for legacy_request in LEGACY_MODEL_REQUESTS:
            if legacy_request in source:
                return source.replace(legacy_request, MODEL_REQUEST_NEW, 1), True
        return source, False

    # Upgrade the first local patch revision, which only handled entering M3.
    if PATCH_MARKER in source:
        patched = source.replace(LEGACY_HELPER, HELPER, 1)
        if patched == source:
            raise ValueError("recognized MiniMax-M3 patch marker but legacy helper was not found")
        for legacy_request in LEGACY_MODEL_REQUESTS:
            if legacy_request in patched:
                patched = patched.replace(legacy_request, MODEL_REQUEST_NEW, 1)
                return patched, True
        raise ValueError("recognized MiniMax-M3 patch marker but legacy model request was not found")

    replacements = (
        ("function QV(", HELPER + "function QV(", 1),
        (QV_OLD, QV_NEW, 1),
        (DOLLAR_V_OLD, DOLLAR_V_NEW, 1),
        (MODEL_REQUEST_OLD, MODEL_REQUEST_NEW, 1),
    )
    patched = source
    for old, new, expected_count in replacements:
        count = patched.count(old)
        if count != expected_count:
            raise ValueError(f"expected {expected_count} occurrence(s), found {count}: {old[:80]}")
        patched = patched.replace(old, new, expected_count)
    return patched, True


def patch_bundle(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    patched, changed = patch_control_ui_bundle(original)
    if changed:
        path.write_text(patched, encoding="utf-8")
    return changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    changed = patch_bundle(args.bundle)
    print(f"{'Patched' if changed else 'Already patched'}: {args.bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
