"""
demo_and_test.py  –  Phase 1 + 2 + 3
══════════════════════════════════════
Builds a synthetic DXF that deliberately contains every Phase 3 edge case:
  • A rectangle with a small gap  (< 1 mm) → must be snapped closed
  • Two identical overlapping rectangles   → must be unioned into one
  • A self-intersecting (figure-8) ring    → must be buffer(0) repaired
  • An open polyline (LINE entity)         → must become Linear Molding
  • A normal closed panel                 → Surface Panel baseline
  • A high-complexity elongated panel     → HIGH COMPLEXITY flag

Run:
    python demo_and_test.py
"""

import math, sys, os
sys.path.insert(0, os.path.dirname(__file__))


# ═══════════════════════════════════════════════════════════════════════════════
#  DXF builder
# ═══════════════════════════════════════════════════════════════════════════════

def create_demo_dxf(path: str):
    try:
        import ezdxf
    except ImportError:
        print("[SKIP] ezdxf not installed.  pip install ezdxf")
        return None

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    doc.layers.new("GFRC-PANEL-20mm",  dxfattribs={"color": 3})   # green
    doc.layers.new("GFRC-PANEL-25mm",  dxfattribs={"color": 5})   # blue
    doc.layers.new("RETURN-SILL",      dxfattribs={"color": 6})   # magenta
    doc.layers.new("COMPLEX-PANEL",    dxfattribs={"color": 2})   # yellow
    doc.layers.new("OPEN-LINES",       dxfattribs={"color": 4})   # cyan

    def crect(layer, x0, y0, w, h, lw=25):
        """Closed rectangular polyline."""
        pts = [(x0,y0),(x0+w,y0),(x0+w,y0+h),(x0,y0+h)]
        p = msp.add_lwpolyline(pts, dxfattribs={"layer": layer, "lineweight": lw})
        p.close(True)

    # ── Case 1 : Normal closed panel (baseline) ───────────────────────────────
    crect("GFRC-PANEL-20mm", 0, 0, 2400, 1200)

    # ── Case 2 : Panel with a small gap (0.6 mm < 1 mm tolerance) ─────────────
    # We do NOT set closed=True; last point is 0.6 mm away from first
    gap_pts = [(5000, 0), (7400, 0), (7400, 1200), (5000.6, 1200)]  # 0.6mm gap on y-return
    msp.add_lwpolyline(gap_pts, dxfattribs={"layer": "GFRC-PANEL-20mm", "lineweight": 25})
    # Note: not calling .close() — gap snap must handle this

    # ── Case 3 : Two identical overlapping rectangles → union into one ─────────
    for _ in range(2):
        crect("GFRC-PANEL-25mm", 0, 2000, 3000, 1500)

    # ── Case 4 : Slightly offset duplicate (50% overlap) ──────────────────────
    crect("GFRC-PANEL-25mm", 200, 2000, 3000, 1500)   # offset 200mm → overlap

    # ── Case 5 : Self-intersecting figure-8 polyline (needs buffer(0) repair) ──
    fig8 = [
        (10000, 0), (12000, 1200), (12000, 0), (10000, 1200), (10000, 0)
    ]
    p = msp.add_lwpolyline(fig8, dxfattribs={"layer": "GFRC-PANEL-20mm", "lineweight": 25})
    p.close(True)

    # ── Case 6 : Open line segments → Linear Molding ──────────────────────────
    for i in range(3):
        msp.add_line(
            (0, 5000 + i*200), (8000, 5000 + i*200),
            dxfattribs={"layer": "OPEN-LINES", "lineweight": 13}
        )

    # ── Case 7 : Return/flange closed loop ────────────────────────────────────
    crect("RETURN-SILL", 0, 7000, 8000, 50)

    # ── Case 8 : High-complexity elongated panel (CF ≈ 20 → > 14 threshold) ───
    crect("COMPLEX-PANEL", 0, 8000, 10000, 100)

    # ── Case 9 : Valid spline-based panel on 20mm layer ───────────────────────
    cx, cy, r = 14000, 600, 400
    ctrl = [(cx + r*math.cos(math.radians(a)), cy + r*math.sin(math.radians(a)), 0)
            for a in range(0, 361, 72)]
    msp.add_spline(ctrl, dxfattribs={"layer": "GFRC-PANEL-20mm"})

    doc.saveas(path)
    print(f"  Demo DXF written → {path}")
    return path


# ═══════════════════════════════════════════════════════════════════════════════
#  Run & assert
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    demo_dxf = "demo_drawing.dxf"
    path = create_demo_dxf(demo_dxf)
    if not path:
        return

    # ── Phase 3 standalone ────────────────────────────────────────────────────
    try:
        from gfrc_geometry import run_phase3_dxf, print_cleanup_report
    except ImportError:
        print("[SKIP] Shapely not installed – pip install shapely")
        # Fall back to Phase 1+2 only
        from gfrc_quantifier import run
        run(filepath=path, phase3=False)
        return

    groups, issues = run_phase3_dxf(demo_dxf, gap_tolerance_mm=1.0)
    print_cleanup_report(groups, issues, demo_dxf)

    # ── Phase 2 on cleaned geometry ───────────────────────────────────────────
    from gfrc_engine import run_phase2
    results = run_phase2(groups, demo_dxf, return_depth_mm=100.0)

    # ── Assertions ────────────────────────────────────────────────────────────
    issue_types = {iss.issue_type for iss in issues}

    assert "GAP_SNAPPED"       in issue_types, "Expected a gap to be snapped"
    assert "OVERLAP_MERGED"    in issue_types, "Expected overlap to be merged"
    assert "NON_PLANAR"        in issue_types or \
           "DEGENERATE_DROPPED" in issue_types, \
           "Expected open/degenerate entity to be caught"

    surface_groups  = [g for g in groups if g.classification == "Surface Panel"]
    linear_groups   = [g for g in groups if g.classification == "Linear Molding"]
    assert len(surface_groups) >= 1, "Expected Surface Panel groups"
    assert len(linear_groups)  >= 1, "Expected Linear Molding groups"

    # Weight sanity
    for r in results:
        assert r.panel_weight_kg >= 0, f"Negative weight: {r.layer}"
        if r.panel_weight_kg > 0:
            assert abs(r.fiber_weight_kg / r.panel_weight_kg - 0.05) < 1e-9

    # High complexity
    high = [r for r in results if "HIGH" in r.complexity_flag]
    assert len(high) >= 1, "Expected HIGH COMPLEXITY panel"

    print("\n  ✓ All Phase 1 + 2 + 3 assertions passed.\n")


if __name__ == "__main__":
    main()
