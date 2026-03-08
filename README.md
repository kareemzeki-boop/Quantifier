# GFRC Quantification Tool

> Glass Fiber Reinforced Concrete panel takeoff tool — Phases 1 through 4.

Parses **AutoCAD DXF** and **vector PDF** drawings, cleans geometry with Shapely, maps materials, computes panel weights and costs, and presents everything in a Streamlit dashboard with CSV/Excel export.

---

## 📁 File Structure

```
gfrc-quantification-tool/
│
├── app.py                  ← Phase 4: Streamlit dashboard (run this)
├── gfrc_quantifier.py      ← Phase 1: DXF / PDF parser
├── gfrc_engine.py          ← Phase 2: Material mapping & calculations
├── gfrc_geometry.py        ← Phase 3: Shapely cleanup & classification
├── process_drawing.py      ← Express API bridge (outputs clean JSON)
├── routes_fix.ts           ← Drop-in Express route fix (stdout/stderr split)
├── demo_and_test.py        ← Generates a synthetic DXF and runs all assertions
├── requirements.txt        ← All Python dependencies
└── README.md
```

---

## 🚀 Quick Start

### Option A — Streamlit app (recommended)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Launch the dashboard
streamlit run app.py
```

Open http://localhost:8501, upload a DXF or PDF, and use the sidebar to configure costs and tolerances.

### Option B — CLI (headless / scripting)

```bash
# Full pipeline (Phase 1 + 2 + 3)
python gfrc_quantifier.py drawing.dxf

# Phase 1 geometry only
python gfrc_quantifier.py drawing.dxf --p1-only

# Skip Shapely cleanup
python gfrc_quantifier.py drawing.dxf --no-p3

# PDF
python gfrc_quantifier.py drawing.pdf
```

### Option C — Express / Node.js backend

Point your Express route at `process_drawing.py`. See `routes_fix.ts` for the correct spawn + stdout/stderr handling pattern.

```bash
python process_drawing.py drawing.dxf '{"cost_gfrc":2.80,"cost_formwork":180}'
# → single JSON line on stdout, all logs on stderr
```

### Option D — Run demo & self-test

```bash
python demo_and_test.py
```

Generates a synthetic DXF with every edge case (gap, overlap, complexity, return/flange) and asserts all pipeline outputs are correct.

---

## 🔧 Dependencies

```
ezdxf>=1.1.0        DXF parsing
pymupdf>=1.23.0     PDF parsing  (imports as 'fitz')
tabulate>=0.9.0     Console table output
shapely>=2.0.0      Phase 3 geometry cleanup
streamlit>=1.35.0   Dashboard
plotly>=5.20.0      Interactive charts
pandas>=2.0.0       DataFrames and export
openpyxl>=3.1.0     Excel (.xlsx) export
```

Install all at once:
```bash
pip install -r requirements.txt
```

---

## 🏗️ Phase Overview

### Phase 1 — Parser (`gfrc_quantifier.py`)
- Reads DXF Model Space for `LWPOLYLINE`, `POLYLINE`, `SPLINE` entities
- Extracts closed vector paths from PDFs via PyMuPDF
- Groups by **Layer Name**, **Hex Color**, **Lineweight**
- PDF **Scale Calibration**: input a known distance to set a px→mm ratio
- Outputs: entity count, total perimeter (mm), total enclosed area (mm²)

### Phase 2 — Calculation Engine (`gfrc_engine.py`)
| Output | Formula |
|---|---|
| Panel Weight | `Area × Thickness × Density (2100 kg/m³)` |
| Glass Fiber | `Panel Weight × dosage (default 5%)` |
| Return / Flange Area | `Perimeter × Return Depth` (for `RETURN`/`FLANGE` layers) |
| Complexity Factor | `Perimeter ÷ √Area` — flagged **HIGH** when > 14.0 |

**Material mapping priority:**
1. Layer name keyword (`20mm`, `25mm`, `30mm` … in layer name)
2. Hex colour (`#FF0000` / any red → 20 mm by default)
3. Configurable fallback default (default 20 mm)

### Phase 3 — Geometry Cleanup (`gfrc_geometry.py`)
Requires `shapely`. Degrades gracefully to Phase 1 if not installed.

| Step | What it does |
|---|---|
| Gap Snap | Closes polylines whose endpoints are within `gap_tolerance_mm` (default 1 mm) |
| Validity Repair | Applies Shapely `buffer(0)` to fix self-intersecting rings |
| Planarity Check | Open / degenerate rings → **Linear Molding** (not Surface Panel) |
| Boolean Union | `unary_union()` merges overlapping/duplicate polygons per layer group |
| Classification | `Surface Panel` or `Linear Molding` |

### Phase 4 — Streamlit Dashboard (`app.py`)
- **Sidebar**: Unit costs (£/kg GFRC, £/m² formwork), material settings, cleanup tolerances
- **KPI cards**: Volume (m³), Weight (t), Fiber (kg), Total Cost (£), High Complexity count
- **Charts**: Weight by layer, cost donut, area by thickness, complexity scatter
- **Layer table**: filterable with live cost totals
- **Export**: CSV (bidding sheet), Excel (3 sheets: breakdown / summary / by-thickness), Cleanup log CSV

---

## ⚙️ Configuration Reference

### `run()` parameters (`gfrc_quantifier.py`)
| Parameter | Default | Description |
|---|---|---|
| `gap_tolerance_mm` | `1.0` | Phase 3 snap tolerance |
| `area_threshold_mm2` | `1.0` | Minimum polygon area (noise filter) |
| `return_depth_mm` | `100.0` | Return/flange depth |
| `fiber_dosage` | `0.05` | Glass fiber as fraction of weight |
| `complexity_threshold` | `14.0` | Perimeter/√Area flag threshold |
| `phase3` | `True` | Enable Shapely cleanup |
| `phase2` | `True` | Enable calculation engine |

### `process_drawing.py` JSON options
```json
{
  "gap_tol":           1.0,
  "area_floor":        1.0,
  "return_depth":      100.0,
  "fiber_dosage":      0.05,
  "complexity_thresh": 14.0,
  "density":           2100.0,
  "default_thickness": 20.0,
  "use_phase3":        true,
  "cost_gfrc":         2.80,
  "cost_formwork":     180.0
}
```

---

## 🐛 Common Issues

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: fitz` | `pip install pymupdf` (not `fitz`) |
| `ModuleNotFoundError: shapely` | `pip install shapely` — Phase 3 is optional, app degrades gracefully |
| Express `Unexpected token` JSON error | Use `process_drawing.py` + `routes_fix.ts` — all `print()` redirected to stderr |
| Replit Data App port | Add `--server.port=8080 --server.address=0.0.0.0` to streamlit run command |
| No entities found in DXF | Ensure polylines have `.closed = True` flag; open polylines → Linear Molding |

---

## 🗺️ Roadmap

- [ ] GUI canvas with click-to-calibrate PDF scale
- [ ] DXF block/xref expansion
- [ ] Arc-segment length correction for bulge polylines
- [ ] Panel nesting / cutting optimisation
- [ ] IFC / Revit export
- [ ] Multi-file batch processing
