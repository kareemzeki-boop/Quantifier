"""
app.py  ─  Phase 4: GFRC Quantification Tool – Streamlit Frontend
══════════════════════════════════════════════════════════════════
Run with:
    streamlit run app.py

Requires (on top of Phases 1-3 deps):
    pip install streamlit plotly openpyxl
"""

from __future__ import annotations

import io
import math
import os
import sys
import tempfile
from pathlib import Path

import streamlit as st

# ── Make sibling modules importable regardless of cwd ────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE CONFIG  (must be first Streamlit call)
# ═══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title  = "GFRC Quantification Tool",
    page_icon   = "🏗️",
    layout      = "wide",
    initial_sidebar_state = "expanded",
)

# ═══════════════════════════════════════════════════════════════════════════════
#  CUSTOM CSS
# ═══════════════════════════════════════════════════════════════════════════════

st.markdown("""
<style>
/* ── Global ── */
[data-testid="stAppViewContainer"] { background: #0f1117; }
[data-testid="stSidebar"]          { background: #1a1d2e; border-right: 1px solid #2d3154; }
h1, h2, h3                         { color: #e2e8f0; }

/* ── Metric cards ── */
.metric-card {
    background: linear-gradient(135deg, #1e2235 0%, #252a42 100%);
    border: 1px solid #3d4470;
    border-radius: 12px;
    padding: 20px 24px;
    text-align: center;
    box-shadow: 0 4px 20px rgba(0,0,0,0.4);
    transition: transform 0.2s;
}
.metric-card:hover { transform: translateY(-2px); }
.metric-value { font-size: 2.4rem; font-weight: 700; color: #7eb3ff; line-height: 1.1; }
.metric-label { font-size: 0.78rem; color: #8892b0; margin-top: 4px; letter-spacing: 0.08em; text-transform: uppercase; }
.metric-sub   { font-size: 0.9rem; color: #a0aec0; margin-top: 6px; }

/* ── Cost cards ── */
.cost-card {
    background: linear-gradient(135deg, #1b2a1b 0%, #1e3320 100%);
    border: 1px solid #2d5a2d;
    border-radius: 12px;
    padding: 18px 22px;
    text-align: center;
    box-shadow: 0 4px 20px rgba(0,0,0,0.4);
}
.cost-value { font-size: 2rem; font-weight: 700; color: #68d391; line-height: 1.1; }
.cost-label { font-size: 0.78rem; color: #68c68a; margin-top: 4px; letter-spacing: 0.08em; text-transform: uppercase; }

/* ── Warning card ── */
.warn-card {
    background: linear-gradient(135deg, #2a1b1b 0%, #331e1e 100%);
    border: 1px solid #7b3535;
    border-radius: 12px;
    padding: 18px 22px;
    text-align: center;
}
.warn-value { font-size: 2rem; font-weight: 700; color: #fc8181; line-height: 1.1; }
.warn-label { font-size: 0.78rem; color: #fc8181; margin-top: 4px; letter-spacing: 0.08em; text-transform: uppercase; }

/* ── Section header ── */
.section-header {
    font-size: 1.05rem; font-weight: 600; color: #7eb3ff;
    border-bottom: 1px solid #3d4470; padding-bottom: 6px; margin-bottom: 14px;
    letter-spacing: 0.04em;
}

/* ── Upload zone ── */
[data-testid="stFileUploader"] {
    background: #1a1d2e; border: 2px dashed #3d4470;
    border-radius: 10px; padding: 12px;
}

/* ── Sidebar sliders ── */
[data-testid="stSlider"] .st-cc { color: #7eb3ff; }

/* ── Issue badge ── */
.badge-high { background: #7b3535; color: #fc8181; padding: 2px 8px; border-radius: 4px; font-size: 0.78rem; font-weight: 600; }
.badge-std  { background: #1e3320; color: #68d391; padding: 2px 8px; border-radius: 4px; font-size: 0.78rem; font-weight: 600; }
.badge-lin  { background: #2a2520; color: #f6ad55; padding: 2px 8px; border-radius: 4px; font-size: 0.78rem; font-weight: 600; }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _mm2_to_m2(v: float) -> float: return v / 1_000_000
def _mm3_to_m3(v: float) -> float: return v / 1_000_000_000
def _kg_to_t(v: float)  -> float: return v / 1000

def metric_card(label: str, value: str, sub: str = "", style: str = "blue") -> str:
    if style == "green":
        return f"""<div class="cost-card">
            <div class="cost-value">{value}</div>
            <div class="cost-label">{label}</div>
            {'<div class="metric-sub">' + sub + '</div>' if sub else ''}
        </div>"""
    elif style == "red":
        return f"""<div class="warn-card">
            <div class="warn-value">{value}</div>
            <div class="warn-label">{label}</div>
            {'<div class="metric-sub">' + sub + '</div>' if sub else ''}
        </div>"""
    return f"""<div class="metric-card">
        <div class="metric-value">{value}</div>
        <div class="metric-label">{label}</div>
        {'<div class="metric-sub">' + sub + '</div>' if sub else ''}
    </div>"""


@st.cache_data(show_spinner=False)
def run_pipeline(
    file_bytes: bytes,
    filename: str,
    gap_tol: float,
    area_floor: float,
    return_depth: float,
    fiber_dosage: float,
    complexity_thresh: float,
    density: float,
    use_phase3: bool,
) -> dict:
    """
    Cache-wrapped full pipeline. Returns a dict with keys:
      groups, results, issues, source_name
    """
    suffix = Path(filename).suffix.lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        # ── Phase 3 (Shapely cleanup) ─────────────────────────────────────
        issues = []
        if use_phase3:
            try:
                from gfrc_geometry import run_phase3_dxf, run_phase3_pdf
                if suffix == ".dxf":
                    groups, issues = run_phase3_dxf(
                        tmp_path, gap_tolerance_mm=gap_tol, area_threshold_mm2=area_floor
                    )
                else:
                    # PDF: use a default 1:1 scale (user calibrates externally)
                    groups, issues = run_phase3_pdf(
                        tmp_path, px_per_mm=1.0,
                        gap_tolerance_mm=gap_tol, area_threshold_mm2=area_floor
                    )
            except ImportError:
                use_phase3 = False

        if not use_phase3:
            if suffix == ".dxf":
                from gfrc_quantifier import parse_dxf
                groups = parse_dxf(tmp_path)
            else:
                from gfrc_quantifier import parse_pdf, ScaleCalibration
                cal = ScaleCalibration(px_distance=1.0, real_mm=1.0)
                groups = parse_pdf(tmp_path, cal)

        # ── Phase 2 (Calculation engine) ──────────────────────────────────
        from gfrc_engine import GFRCEngine, MaterialMapper
        mapper = MaterialMapper(density_kg_m3=density)
        engine = GFRCEngine(
            mapper               = mapper,
            fiber_dosage         = fiber_dosage,
            return_depth_mm      = return_depth,
            complexity_threshold = complexity_thresh,
        )
        results = engine.process(groups)

    finally:
        os.unlink(tmp_path)

    return {
        "groups":  groups,
        "results": results,
        "issues":  issues,
        "source":  filename,
        "p3_used": use_phase3,
    }


def build_export_df(results, cost_gfrc: float, cost_formwork: float):
    """Build a pandas DataFrame for CSV / Excel export."""
    import pandas as pd

    rows = []
    for r in results:
        face_area_mm2 = r.return_area_mm2 if r.is_return else r.total_area_mm2
        face_area_m2  = _mm2_to_m2(face_area_mm2)
        vol_m3        = face_area_m2 * (r.thickness_mm / 1000)
        gfrc_cost     = r.panel_weight_kg * cost_gfrc
        fw_cost       = face_area_m2 * cost_formwork
        total_cost    = gfrc_cost + fw_cost
        cf_str = f"{r.complexity_factor:.2f}" if math.isfinite(r.complexity_factor) else "∞"

        classification = getattr(r, "classification", "Surface Panel") \
            if hasattr(r, "classification") else "Surface Panel"

        rows.append({
            "Layer / Page"         : r.layer,
            "Entity Type"          : r.entity_type,
            "Classification"       : classification,
            "Hex Color"            : r.hex_color,
            "Thickness (mm)"       : r.thickness_mm,
            "Panel Count"          : r.count,
            "Perimeter (mm)"       : round(r.total_length_mm, 2),
            "Face Area (mm²)"      : round(face_area_mm2, 2),
            "Face Area (m²)"       : round(face_area_m2, 6),
            "Volume (m³)"          : round(vol_m3, 8),
            "Panel Weight (kg)"    : round(r.panel_weight_kg, 4),
            "Fiber Weight (kg)"    : round(r.fiber_weight_kg, 4),
            "Is Return / Flange"   : "YES" if r.is_return else "NO",
            "Return Area (mm²)"    : round(r.return_area_mm2, 2),
            "Complexity Factor"    : cf_str,
            "Complexity Flag"      : r.complexity_flag,
            "Material Mapped By"   : r.match_reason,
            "GFRC Material Cost"   : round(gfrc_cost, 2),
            "Formwork Cost"        : round(fw_cost, 2),
            "Total Line Cost"      : round(total_cost, 2),
        })

    return pd.DataFrame(rows)


def to_csv_bytes(df) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def to_excel_bytes(df, results, cost_gfrc: float, cost_formwork: float) -> bytes:
    import pandas as pd
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # ── Sheet 1: Full breakdown ────────────────────────────────────
        df.to_excel(writer, index=False, sheet_name="Layer Breakdown")
        ws = writer.sheets["Layer Breakdown"]
        # Basic column widths
        for col in ws.columns:
            max_len = max(len(str(c.value or "")) for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

        # ── Sheet 2: Project Summary ───────────────────────────────────
        total_weight = sum(r.panel_weight_kg for r in results)
        total_fiber  = sum(r.fiber_weight_kg for r in results)
        total_vol    = sum(
            _mm2_to_m2(r.return_area_mm2 if r.is_return else r.total_area_mm2)
            * (r.thickness_mm / 1000) for r in results
        )
        total_area   = sum(
            _mm2_to_m2(r.return_area_mm2 if r.is_return else r.total_area_mm2)
            for r in results
        )
        total_gfrc_cost = sum(r.panel_weight_kg * cost_gfrc for r in results)
        total_fw_cost   = sum(
            _mm2_to_m2(r.return_area_mm2 if r.is_return else r.total_area_mm2)
            * cost_formwork for r in results
        )
        summary = pd.DataFrame([
            ["Total Face Area (m²)",        round(total_area, 4)],
            ["Total GFRC Volume (m³)",       round(total_vol, 6)],
            ["Total Project Weight (kg)",    round(total_weight, 3)],
            ["Total Project Weight (t)",     round(_kg_to_t(total_weight), 4)],
            ["Total Glass Fiber (kg)",       round(total_fiber, 3)],
            ["Cost per kg GFRC (£)",         cost_gfrc],
            ["Cost per m² Formwork (£)",     cost_formwork],
            ["Total GFRC Material Cost (£)", round(total_gfrc_cost, 2)],
            ["Total Formwork Cost (£)",      round(total_fw_cost, 2)],
            ["TOTAL PROJECT COST (£)",       round(total_gfrc_cost + total_fw_cost, 2)],
        ], columns=["Metric", "Value"])
        summary.to_excel(writer, index=False, sheet_name="Project Summary")

        # ── Sheet 3: By Thickness ──────────────────────────────────────
        thick_rows = []
        thicknesses = sorted({r.thickness_mm for r in results})
        for t in thicknesses:
            sub = [r for r in results if r.thickness_mm == t]
            w  = sum(r.panel_weight_kg for r in sub)
            a  = sum(_mm2_to_m2(r.return_area_mm2 if r.is_return else r.total_area_mm2) for r in sub)
            v  = sum(_mm2_to_m2(r.return_area_mm2 if r.is_return else r.total_area_mm2) * (r.thickness_mm/1000) for r in sub)
            f  = sum(r.fiber_weight_kg for r in sub)
            thick_rows.append({
                "Thickness (mm)" : t, "Panel Count": sum(r.count for r in sub),
                "Face Area (m²)" : round(a,4), "Volume (m³)": round(v,6),
                "Weight (kg)"    : round(w,3),  "Fiber (kg)": round(f,3),
                "GFRC Cost (£)"  : round(w * cost_gfrc, 2),
                "Formwork Cost (£)": round(a * cost_formwork, 2),
            })
        pd.DataFrame(thick_rows).to_excel(writer, index=False, sheet_name="By Thickness")

    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## 🏗️ GFRC Tool")
    st.markdown("---")

    # ── File uploader ─────────────────────────────────────────────────────────
    st.markdown('<div class="section-header">📂 File Upload</div>', unsafe_allow_html=True)
    uploaded = st.file_uploader(
        "Upload DXF or PDF",
        type=["dxf", "pdf"],
        help="Accepts AutoCAD DXF and vector PDF drawings",
    )

    st.markdown("---")

    # ── Unit Costs ────────────────────────────────────────────────────────────
    st.markdown('<div class="section-header">💷 Unit Costs</div>', unsafe_allow_html=True)

    cost_gfrc = st.number_input(
        "GFRC material cost (£ / kg)",
        min_value=0.0, max_value=10000.0, value=2.80, step=0.05,
        format="%.2f",
        help="Blended unit rate per kg of mixed GFRC including admixtures",
    )
    cost_formwork = st.number_input(
        "Formwork cost (£ / m²)",
        min_value=0.0, max_value=50000.0, value=180.0, step=5.0,
        format="%.2f",
        help="Cost per m² of mould / formwork fabrication",
    )

    st.markdown("---")

    # ── Material Settings ─────────────────────────────────────────────────────
    st.markdown('<div class="section-header">⚗️ Material Settings</div>', unsafe_allow_html=True)

    density = st.number_input(
        "GFRC density (kg/m³)",
        min_value=1500.0, max_value=2800.0, value=2100.0, step=50.0,
        format="%.0f",
    )
    fiber_pct = st.slider(
        "Glass fiber dosage (%)",
        min_value=1.0, max_value=10.0, value=5.0, step=0.5,
    )
    return_depth = st.number_input(
        "Return / Flange depth (mm)",
        min_value=10.0, max_value=500.0, value=100.0, step=10.0,
    )
    default_thick = st.number_input(
        "Default panel thickness (mm)",
        min_value=5.0, max_value=100.0, value=20.0, step=5.0,
        help="Used when no thickness rule matches the layer name",
    )

    st.markdown("---")

    # ── Geometry Cleanup ──────────────────────────────────────────────────────
    st.markdown('<div class="section-header">🔧 Geometry Cleanup (Phase 3)</div>', unsafe_allow_html=True)

    use_p3 = st.toggle("Enable Shapely cleanup", value=True,
                       help="Requires: pip install shapely")
    gap_tol = st.slider(
        "Gap snap tolerance (mm)",
        min_value=0.1, max_value=5.0, value=1.0, step=0.1,
        disabled=not use_p3,
    )
    area_floor = st.slider(
        "Min polygon area (mm²)",
        min_value=0.1, max_value=100.0, value=1.0, step=0.5,
        disabled=not use_p3,
    )
    complexity_thresh = st.slider(
        "Complexity flag threshold",
        min_value=5.0, max_value=30.0, value=14.0, step=0.5,
        help="Perimeter / √Area ratio above which panel is flagged HIGH COMPLEXITY",
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN CONTENT
# ═══════════════════════════════════════════════════════════════════════════════

st.markdown(
    "<h1 style='color:#e2e8f0; margin-bottom:0'>🏗️ GFRC Quantification Tool</h1>"
    "<p style='color:#8892b0; margin-top:4px; font-size:0.9rem'>"
    "Phase 1–4 · Geometry Extraction · Material Mapping · Cost Estimation</p>",
    unsafe_allow_html=True,
)
st.markdown("---")

# ── No file uploaded ──────────────────────────────────────────────────────────
if uploaded is None:
    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("""
        <div style="background:#1a1d2e; border:1px solid #3d4470; border-radius:12px; padding:32px; text-align:center; margin-top:40px">
            <div style="font-size:3rem">📐</div>
            <h3 style="color:#7eb3ff; margin-top:16px">Upload a Drawing to Begin</h3>
            <p style="color:#8892b0">Supports AutoCAD DXF and vector PDF files.</p>
            <p style="color:#8892b0; font-size:0.85rem">Use the sidebar uploader to load your GFRC panel drawing.</p>
        </div>
        """, unsafe_allow_html=True)
    with c2:
        st.markdown("""
        <div style="background:#1a1d2e; border:1px solid #3d4470; border-radius:12px; padding:28px; margin-top:40px">
            <h4 style="color:#e2e8f0">Pipeline Overview</h4>
            <p style="color:#8892b0; font-size:0.88rem; line-height:1.8">
            <b style="color:#7eb3ff">Phase 1</b> – Parse DXF / PDF, extract closed polylines & splines<br>
            <b style="color:#7eb3ff">Phase 2</b> – Map materials, compute weight / fiber / complexity<br>
            <b style="color:#7eb3ff">Phase 3</b> – Shapely cleanup: gap snap, union, planarity check<br>
            <b style="color:#7eb3ff">Phase 4</b> – This dashboard: costs, charts, CSV/Excel export
            </p>
        </div>
        """, unsafe_allow_html=True)
    st.stop()

# ── Run pipeline ──────────────────────────────────────────────────────────────
with st.spinner("Processing drawing …"):
    try:
        pipe = run_pipeline(
            file_bytes        = uploaded.getvalue(),
            filename          = uploaded.name,
            gap_tol           = gap_tol,
            area_floor        = area_floor,
            return_depth      = return_depth,
            fiber_dosage      = fiber_pct / 100,
            complexity_thresh = complexity_thresh,
            density           = density,
            use_phase3        = use_p3,
        )
    except Exception as exc:
        st.error(f"❌ Pipeline error: {exc}")
        st.exception(exc)
        st.stop()

results = pipe["results"]
issues  = pipe["issues"]
groups  = pipe["groups"]
p3_used = pipe["p3_used"]

if not results:
    st.warning("⚠️ No qualifying entities found in this file. Check layer names and entity types.")
    st.stop()

# ── Aggregate totals ──────────────────────────────────────────────────────────
total_weight_kg = sum(r.panel_weight_kg for r in results)
total_fiber_kg  = sum(r.fiber_weight_kg for r in results)
total_vol_m3 = sum(
    _mm2_to_m2(r.return_area_mm2 if r.is_return else r.total_area_mm2)
    * (r.thickness_mm / 1000) for r in results
)
total_area_m2 = sum(
    _mm2_to_m2(r.return_area_mm2 if r.is_return else r.total_area_mm2)
    for r in results
)
total_gfrc_cost = total_weight_kg * cost_gfrc
total_fw_cost   = total_area_m2   * cost_formwork
total_cost      = total_gfrc_cost + total_fw_cost
high_cx_count   = sum(1 for r in results if "HIGH" in r.complexity_flag)
panel_count     = sum(r.count for r in results)

# ── Phase 3 banner ────────────────────────────────────────────────────────────
if p3_used:
    n_issues = len(issues)
    n_snaps  = sum(getattr(g, "gaps_snapped",   0) for g in groups)
    n_merged = sum(1 for g in groups if getattr(g, "merged_overlap", False))
    st.success(
        f"✅ **Phase 3 Shapely cleanup active** — "
        f"{n_snaps} gap(s) snapped · {n_merged} overlap(s) merged · {n_issues} issue(s) logged"
    )
else:
    st.info("ℹ️ Phase 3 geometry cleanup disabled (install shapely to enable).")


# ═══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD  –  KPI CARDS
# ═══════════════════════════════════════════════════════════════════════════════

st.markdown(
    '<div class="section-header" style="font-size:1.1rem; margin-top:8px">📊 Project Summary</div>',
    unsafe_allow_html=True,
)

# Row 1: Physical quantities
c1, c2, c3, c4, c5 = st.columns(5)
cards = [
    (c1, "Total GFRC Volume",   f"{total_vol_m3:.4f} m³",       f"{total_vol_m3*1000:.1f} litres",     "blue"),
    (c2, "Total Face Area",     f"{total_area_m2:.2f} m²",      f"{panel_count} panels / groups",       "blue"),
    (c3, "Project Weight",      f"{_kg_to_t(total_weight_kg):.3f} t",
                                f"{total_weight_kg:,.1f} kg",   "blue"),
    (c4, "Glass Fiber Required",f"{total_fiber_kg:,.2f} kg",    f"@ {fiber_pct:.1f}% dosage",           "blue"),
    (c5, "High Complexity ⚠",  str(high_cx_count),              "panels flagged",                       "red" if high_cx_count else "blue"),
]
for col, label, value, sub, style in cards:
    col.markdown(metric_card(label, value, sub, style), unsafe_allow_html=True)

st.markdown("<div style='margin-top:12px'></div>", unsafe_allow_html=True)

# Row 2: Cost breakdown
c1, c2, c3, c4 = st.columns(4)
cost_cards = [
    (c1, "GFRC Material Cost",  f"£{total_gfrc_cost:,.2f}", f"£{cost_gfrc:.2f} / kg",          "green"),
    (c2, "Formwork Cost",       f"£{total_fw_cost:,.2f}",   f"£{cost_formwork:.2f} / m²",       "green"),
    (c3, "Total Project Cost",  f"£{total_cost:,.2f}",      f"£{total_cost/total_area_m2:.2f} / m² avg" if total_area_m2 else "", "green"),
    (c4, "Cost per Tonne",      f"£{total_cost/_kg_to_t(total_weight_kg):,.0f}" if total_weight_kg else "—",
                                "GFRC all-in", "green"),
]
for col, label, value, sub, style in cost_cards:
    col.markdown(metric_card(label, value, sub, style), unsafe_allow_html=True)

st.markdown("---")

# ═══════════════════════════════════════════════════════════════════════════════
#  CHARTS
# ═══════════════════════════════════════════════════════════════════════════════

try:
    import plotly.graph_objects as go
    import plotly.express as px
    _PLOTLY = True
except ImportError:
    _PLOTLY = False

if _PLOTLY:
    ch1, ch2 = st.columns(2)

    # ── Chart 1: Weight by Layer ───────────────────────────────────────────
    with ch1:
        st.markdown('<div class="section-header">⚖️ Panel Weight by Layer</div>', unsafe_allow_html=True)
        layers  = [r.layer for r in results]
        weights = [r.panel_weight_kg for r in results]
        fibers  = [r.fiber_weight_kg for r in results]

        fig1 = go.Figure()
        fig1.add_bar(name="GFRC Panel", x=layers, y=weights,
                     marker_color="#7eb3ff", marker_line_width=0)
        fig1.add_bar(name="Glass Fiber", x=layers, y=fibers,
                     marker_color="#68d391", marker_line_width=0)
        fig1.update_layout(
            barmode="group", paper_bgcolor="#0f1117", plot_bgcolor="#0f1117",
            font_color="#e2e8f0", legend=dict(bgcolor="#1a1d2e"),
            xaxis=dict(showgrid=False, tickangle=-35),
            yaxis=dict(gridcolor="#2d3154", title="kg"),
            margin=dict(t=10, b=0, l=0, r=0), height=320,
        )
        st.plotly_chart(fig1, use_container_width=True)

    # ── Chart 2: Cost breakdown donut ─────────────────────────────────────
    with ch2:
        st.markdown('<div class="section-header">💷 Cost Breakdown</div>', unsafe_allow_html=True)
        fig2 = go.Figure(go.Pie(
            labels  = ["GFRC Material", "Formwork"],
            values  = [total_gfrc_cost, total_fw_cost],
            hole    = 0.55,
            marker  = dict(colors=["#7eb3ff", "#68d391"],
                           line=dict(color="#0f1117", width=3)),
            textinfo= "label+percent",
            textfont= dict(color="#e2e8f0", size=13),
        ))
        fig2.update_layout(
            paper_bgcolor="#0f1117", plot_bgcolor="#0f1117",
            font_color="#e2e8f0",
            showlegend=False,
            annotations=[dict(text=f"£{total_cost:,.0f}", x=0.5, y=0.5,
                             font_size=20, font_color="#e2e8f0", showarrow=False)],
            margin=dict(t=10, b=0, l=0, r=0), height=320,
        )
        st.plotly_chart(fig2, use_container_width=True)

    # ── Chart 3: Area by Thickness ────────────────────────────────────────
    ch3, ch4 = st.columns(2)
    with ch3:
        st.markdown('<div class="section-header">📐 Face Area by Thickness</div>', unsafe_allow_html=True)
        from collections import defaultdict
        thick_area: dict[str, float] = defaultdict(float)
        for r in results:
            key = f"{r.thickness_mm:.0f}mm"
            fa  = _mm2_to_m2(r.return_area_mm2 if r.is_return else r.total_area_mm2)
            thick_area[key] += fa

        labels = sorted(thick_area, key=lambda k: float(k.rstrip("mm")))
        values = [thick_area[l] for l in labels]
        colors = px.colors.sequential.Blues_r[:len(labels)] if len(labels) <= 9 \
                 else px.colors.qualitative.Pastel

        fig3 = go.Figure(go.Pie(
            labels=labels, values=values, hole=0.45,
            marker=dict(colors=colors, line=dict(color="#0f1117", width=3)),
            textinfo="label+percent", textfont=dict(color="#e2e8f0", size=12),
        ))
        fig3.update_layout(
            paper_bgcolor="#0f1117", font_color="#e2e8f0",
            showlegend=True, legend=dict(bgcolor="#1a1d2e"),
            margin=dict(t=10, b=0, l=0, r=0), height=300,
        )
        st.plotly_chart(fig3, use_container_width=True)

    # ── Chart 4: Complexity scatter ────────────────────────────────────────
    with ch4:
        st.markdown('<div class="section-header">🔴 Complexity Analysis</div>', unsafe_allow_html=True)
        valid_cx = [(r.layer, r.complexity_factor, r.total_area_mm2, r.complexity_flag)
                    for r in results if math.isfinite(r.complexity_factor)]

        if valid_cx:
            cx_labels, cx_vals, cx_areas, cx_flags = zip(*valid_cx)
            cx_colors = ["#fc8181" if "HIGH" in f else "#68d391" for f in cx_flags]
            fig4 = go.Figure(go.Scatter(
                x=list(cx_vals),
                y=[_mm2_to_m2(a) for a in cx_areas],
                mode="markers+text",
                text=list(cx_labels),
                textposition="top center",
                textfont=dict(color="#8892b0", size=10),
                marker=dict(size=14, color=cx_colors,
                            line=dict(color="#0f1117", width=2)),
                hovertemplate="<b>%{text}</b><br>CF: %{x:.2f}<br>Area: %{y:.4f} m²<extra></extra>",
            ))
            fig4.add_vline(x=complexity_thresh, line_dash="dash",
                           line_color="#f6ad55", annotation_text="Threshold",
                           annotation_font_color="#f6ad55")
            fig4.update_layout(
                paper_bgcolor="#0f1117", plot_bgcolor="#0f1117",
                font_color="#e2e8f0",
                xaxis=dict(title="Complexity Factor", gridcolor="#2d3154"),
                yaxis=dict(title="Face Area (m²)", gridcolor="#2d3154"),
                margin=dict(t=10, b=0, l=0, r=0), height=300,
            )
            st.plotly_chart(fig4, use_container_width=True)

st.markdown("---")

# ═══════════════════════════════════════════════════════════════════════════════
#  DETAIL TABLE
# ═══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="section-header" style="font-size:1.1rem">🗂️ Layer Breakdown</div>',
            unsafe_allow_html=True)

import pandas as pd

df = build_export_df(results, cost_gfrc, cost_formwork)

# Add classification badge column (display only)
def _badge(row):
    cf = row.get("Complexity Flag", "")
    cl = row.get("Classification", "Surface Panel")
    if "HIGH" in str(cf):
        return "⚠️ High Complexity"
    if cl == "Linear Molding":
        return "📏 Linear Molding"
    return "✅ Surface Panel"

display_df = df.copy()
display_df.insert(2, "Status", display_df.apply(_badge, axis=1))

# Column subset for table display
show_cols = [
    "Layer / Page", "Classification", "Status", "Thickness (mm)",
    "Panel Count", "Face Area (m²)", "Volume (m³)",
    "Panel Weight (kg)", "Fiber Weight (kg)",
    "GFRC Material Cost", "Formwork Cost", "Total Line Cost",
    "Complexity Factor",
]
show_cols = [c for c in show_cols if c in display_df.columns]

# Filters
col_f1, col_f2 = st.columns(2)
with col_f1:
    layer_filter = st.multiselect(
        "Filter by Layer",
        options=sorted(display_df["Layer / Page"].unique()),
        default=[],
        placeholder="All layers",
    )
with col_f2:
    class_filter = st.multiselect(
        "Filter by Classification",
        options=sorted(display_df["Classification"].unique()),
        default=[],
        placeholder="All classifications",
    )

filtered = display_df.copy()
if layer_filter:
    filtered = filtered[filtered["Layer / Page"].isin(layer_filter)]
if class_filter:
    filtered = filtered[filtered["Classification"].isin(class_filter)]

st.dataframe(
    filtered[show_cols].reset_index(drop=True),
    use_container_width=True,
    height=420,
    column_config={
        "Face Area (m²)":      st.column_config.NumberColumn(format="%.4f"),
        "Volume (m³)":         st.column_config.NumberColumn(format="%.6f"),
        "Panel Weight (kg)":   st.column_config.NumberColumn(format="%.3f"),
        "Fiber Weight (kg)":   st.column_config.NumberColumn(format="%.3f"),
        "GFRC Material Cost":  st.column_config.NumberColumn(format="£%.2f"),
        "Formwork Cost":       st.column_config.NumberColumn(format="£%.2f"),
        "Total Line Cost":     st.column_config.NumberColumn(format="£%.2f"),
        "Thickness (mm)":      st.column_config.NumberColumn(format="%.0f mm"),
    },
)

# ── Totals footer ─────────────────────────────────────────────────────────────
st.markdown(
    f"<p style='color:#8892b0; font-size:0.85rem; text-align:right'>"
    f"Showing {len(filtered)} of {len(display_df)} rows &nbsp;·&nbsp; "
    f"Filtered weight: <b style='color:#7eb3ff'>"
    f"{filtered['Panel Weight (kg)'].sum():,.2f} kg</b> &nbsp;·&nbsp; "
    f"Filtered cost: <b style='color:#68d391'>"
    f"£{filtered['Total Line Cost'].sum():,.2f}</b></p>",
    unsafe_allow_html=True,
)

st.markdown("---")

# ═══════════════════════════════════════════════════════════════════════════════
#  CLEANUP ISSUES (Phase 3)
# ═══════════════════════════════════════════════════════════════════════════════

if p3_used and issues:
    with st.expander(f"🔧 Geometry Cleanup Log  ({len(issues)} issues)", expanded=False):
        issue_data = [{
            "Entity #"   : iss.entity_index,
            "Layer"      : iss.layer,
            "Issue Type" : iss.issue_type,
            "Detail"     : iss.detail[:120],
        } for iss in issues]
        st.dataframe(pd.DataFrame(issue_data), use_container_width=True, height=300)


# ═══════════════════════════════════════════════════════════════════════════════
#  EXPORT BUTTONS
# ═══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="section-header" style="font-size:1.1rem">⬇️ Export</div>',
            unsafe_allow_html=True)

exp1, exp2, exp3 = st.columns(3)
base_name = Path(uploaded.name).stem

with exp1:
    st.download_button(
        label     = "📄 Download CSV (Layer Breakdown)",
        data      = to_csv_bytes(df),
        file_name = f"{base_name}_gfrc_breakdown.csv",
        mime      = "text/csv",
        use_container_width=True,
    )

with exp2:
    try:
        xl_bytes = to_excel_bytes(df, results, cost_gfrc, cost_formwork)
        st.download_button(
            label     = "📊 Download Excel (3 Sheets)",
            data      = xl_bytes,
            file_name = f"{base_name}_gfrc_report.xlsx",
            mime      = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    except ImportError:
        st.button("📊 Excel export (pip install openpyxl)", disabled=True,
                  use_container_width=True)

with exp3:
    if p3_used and issues:
        issue_csv = pd.DataFrame([{
            "Entity#": i.entity_index, "Layer": i.layer,
            "IssueType": i.issue_type, "Detail": i.detail,
        } for i in issues]).to_csv(index=False).encode()
        st.download_button(
            label     = "🔧 Download Cleanup Log (CSV)",
            data      = issue_csv,
            file_name = f"{base_name}_cleanup_issues.csv",
            mime      = "text/csv",
            use_container_width=True,
        )
    else:
        st.button("🔧 Cleanup Log (enable Phase 3)", disabled=True,
                  use_container_width=True)

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown(
    "<div style='text-align:center; color:#4a5568; font-size:0.78rem; margin-top:32px'>"
    "GFRC Quantification Tool · Phase 1–4 · ezdxf + PyMuPDF + Shapely + Streamlit"
    "</div>",
    unsafe_allow_html=True,
)
