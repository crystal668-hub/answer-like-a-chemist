#!/usr/bin/env python3
"""
CIF → HTML Crystal Viewer using 3Dmol.js.

No Python dependencies required (pure stdlib).

Usage:
    python cif_to_html.py structure.cif
    python cif_to_html.py https://example.com/structure.cif
    python cif_to_html.py structure.cif -o viewer.html
"""

import argparse
import json
import os
import re
import sys
import urllib.request


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help="CIF file path or URL")
    p.add_argument("-o", "--output",
                   help="Output HTML file (default: <stem>_crystal.html)")
    return p.parse_args()


def load_cif(source: str) -> str:
    if source.startswith("http://") or source.startswith("https://"):
        print(f"Downloading: {source}")
        req = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")
    print(f"Reading: {source}")
    with open(source, encoding="utf-8", errors="replace") as f:
        return f.read()


def _cif_value(patterns: list, text: str) -> str:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip().strip("'\"")
    return "?"


def parse_cif_meta(cif: str) -> dict:
    formula = _cif_value([
        r"_chemical_formula_sum\s+'([^']+)'",
        r'_chemical_formula_sum\s+"([^"]+)"',
        r"_chemical_formula_sum\s+(\S+)",
        r"_chemical_formula_structural\s+'([^']+)'",
        r"_chemical_formula_structural\s+(\S+)",
    ], cif)

    sg = _cif_value([
        r"_symmetry_space_group_name_H-M\s+'([^']+)'",
        r'_symmetry_space_group_name_H-M\s+"([^"]+)"',
        r"_symmetry_space_group_name_H-M\s+(\S.*\S)",
        r"_space_group_name_H-M_alt\s+'([^']+)'",
        r"_space_group\.name_H-M_alt\s+'([^']+)'",
        r"_space_group_name_Hall\s+'([^']+)'",
    ], cif)

    def cell(tag):
        m = re.search(rf"_{tag}\s+([\d.]+)", cif, re.IGNORECASE)
        return m.group(1) if m else "?"

    return {
        "formula":    formula,
        "spacegroup": sg,
        "a":     cell("cell_length_a"),
        "b":     cell("cell_length_b"),
        "c":     cell("cell_length_c"),
        "alpha": cell("cell_angle_alpha"),
        "beta":  cell("cell_angle_beta"),
        "gamma": cell("cell_angle_gamma"),
    }


def generate_html(cif_content: str, meta: dict, source_name: str) -> str:
    cif_json  = json.dumps(cif_content)
    meta_json = json.dumps(meta)
    title     = meta["formula"] if meta["formula"] != "?" else source_name

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title} – Crystal Viewer</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #0d1117; overflow: hidden;
            font-family: 'Segoe UI', system-ui, sans-serif; }}
    #viewport {{ position: fixed; inset: 0; }}

    /* ── Info panel ── */
    #panel {{
      position: fixed; top: 14px; left: 14px; z-index: 10;
      background: rgba(13,17,23,.88); border: 1px solid rgba(255,255,255,.13);
      border-radius: 10px; padding: 14px 18px;
      color: #e6edf3; font-size: 13px; line-height: 1.75;
      min-width: 210px; backdrop-filter: blur(8px);
    }}
    #panel h2 {{ font-size: 17px; font-weight: 700; color: #fff; margin-bottom: 2px; }}
    .lbl {{ color: #8b949e; font-size: 11px; margin-top: 6px; }}
    #cell {{ font-size: 11.5px; white-space: pre; }}

    /* ── Style buttons ── */
    #toolbar {{
      position: fixed; bottom: 14px; right: 14px; z-index: 10;
      display: flex; flex-direction: column; gap: 6px;
    }}
    .btn {{
      background: rgba(13,17,23,.88); border: 1px solid rgba(255,255,255,.15);
      border-radius: 7px; padding: 7px 14px; color: #c9d1d9; font-size: 12px;
      cursor: pointer; backdrop-filter: blur(8px); text-align: center;
      transition: background .15s, border-color .15s;
    }}
    .btn:hover  {{ background: rgba(48,56,72,.9); border-color: rgba(255,255,255,.3); }}
    .btn.active {{ background: rgba(56,139,253,.3); border-color: #388bfd; color: #fff; }}

    /* ── Hint ── */
    #hint {{
      position: fixed; bottom: 14px; left: 14px; z-index: 10;
      background: rgba(13,17,23,.7); border: 1px solid rgba(255,255,255,.1);
      border-radius: 8px; padding: 8px 12px; color: #8b949e; font-size: 11px;
      backdrop-filter: blur(6px); line-height: 1.9;
    }}
  </style>
</head>
<body>
  <div id="viewport"></div>

  <div id="panel">
    <h2 id="fml">–</h2>
    <div class="lbl">Space group</div>
    <div id="sg">–</div>
    <div class="lbl">Cell parameters</div>
    <div id="cell">–</div>
  </div>

  <div id="toolbar">
    <button class="btn active" id="btn-bs"     onclick="setStyle('ballstick')">Ball &amp; Stick</button>
    <button class="btn"        id="btn-sphere" onclick="setStyle('sphere')">Space Filling</button>
    <button class="btn"        id="btn-stick"  onclick="setStyle('stick')">Stick</button>
    <button class="btn"        id="btn-line"   onclick="setStyle('line')">Wire</button>
    <button class="btn"        id="btn-cell"   onclick="toggleCell()">Unit Cell ✓</button>
    <button class="btn"        id="btn-bg"     onclick="toggleBg()">Light BG</button>
  </div>

  <div id="hint">
    Drag&nbsp;&nbsp;rotate &nbsp;|&nbsp; Scroll&nbsp;&nbsp;zoom &nbsp;|&nbsp; Shift+drag&nbsp;&nbsp;pan
  </div>

  <script src="https://3Dmol.org/build/3Dmol-min.js"></script>
  <script>
  (function () {{
    const CIF  = {cif_json};
    const META = {meta_json};

    // ── Info panel ────────────────────────────────────────────────────────
    document.getElementById('fml').textContent = META.formula;
    document.getElementById('sg').textContent  = META.spacegroup;
    document.getElementById('cell').textContent =
      'a=' + META.a + '  b=' + META.b + '  c=' + META.c + ' Å\\n' +
      'α=' + META.alpha + '°  β=' + META.beta + '°  γ=' + META.gamma + '°';

    // ── Viewer ────────────────────────────────────────────────────────────
    let bgDark = true;
    const viewer = $3Dmol.createViewer(
      document.getElementById('viewport'),
      {{ backgroundColor: '#0d1117' }}
    );

    const model = viewer.addModel(CIF, 'cif');

    // ── Styles ────────────────────────────────────────────────────────────
    const STYLES = {{
      ballstick: {{ sphere: {{ scale: 0.3, colorscheme: 'Jmol' }},
                    stick:  {{ radius: 0.12, colorscheme: 'Jmol' }} }},
      sphere:    {{ sphere: {{ colorscheme: 'Jmol' }} }},
      stick:     {{ stick:  {{ radius: 0.15, colorscheme: 'Jmol' }} }},
      line:      {{ line:   {{ colorscheme: 'Jmol' }} }},
    }};

    let currentStyle = 'ballstick';
    viewer.setStyle({{}}, STYLES.ballstick);

    function setStyle(name) {{
      currentStyle = name;
      viewer.setStyle({{}}, STYLES[name]);
      viewer.render();
      ['bs','sphere','stick','line'].forEach(k => {{
        document.getElementById('btn-' + k).classList.remove('active');
      }});
      document.getElementById('btn-' + (name === 'ballstick' ? 'bs' : name))
              .classList.add('active');
    }}

    // ── Unit cell ─────────────────────────────────────────────────────────
    let cellVisible = true;
    viewer.addUnitCell(model, {{
      box: {{ color: 'white', linewidth: 1.5, opacity: 0.45 }},
      alabel: '', blabel: '', clabel: '',
    }});

    function toggleCell() {{
      cellVisible = !cellVisible;
      const btn = document.getElementById('btn-cell');
      if (cellVisible) {{
        viewer.addUnitCell(model, {{
          box: {{ color: bgDark ? 'white' : '#333', linewidth: 1.5, opacity: 0.45 }},
          alabel: '', blabel: '', clabel: '',
        }});
        btn.textContent = 'Unit Cell ✓';
      }} else {{
        viewer.removeUnitCell(model);
        btn.textContent = 'Unit Cell';
      }}
      viewer.render();
    }}

    // ── Background toggle ─────────────────────────────────────────────────
    function toggleBg() {{
      bgDark = !bgDark;
      viewer.setBackgroundColor(bgDark ? '#0d1117' : '#f5f5f5');
      document.getElementById('btn-bg').textContent = bgDark ? 'Light BG' : 'Dark BG';
      document.body.style.background = bgDark ? '#0d1117' : '#f5f5f5';
      if (cellVisible) toggleCell(), toggleCell(); // redraw cell with new color
      viewer.render();
    }}

    // ── Initial render ────────────────────────────────────────────────────
    viewer.zoomTo();
    viewer.render();
  }})();
  </script>
</body>
</html>"""


def main():
    args = parse_args()
    cif = load_cif(args.input)
    meta = parse_cif_meta(cif)

    print(f"Formula: {meta['formula']}  |  Space group: {meta['spacegroup']}")

    stem = os.path.splitext(os.path.basename(args.input.split("?")[0]))[0]
    out = args.output or f"{stem}_crystal.html"

    html = generate_html(cif, meta, stem)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)

    print(f"Saved:  {out}")
    print(f"Open:   file://{os.path.abspath(out)}")


if __name__ == "__main__":
    main()
