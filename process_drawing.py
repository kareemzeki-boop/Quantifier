"""
process_drawing.py  ─  Express API bridge
══════════════════════════════════════════
Called by the Express server as a child process.

Usage:
    python process_drawing.py <filepath> [options as JSON string]

Options JSON example:
    '{"gap_tol":1.0,"area_floor":1.0,"return_depth":100,"fiber_dosage":0.05,
      "complexity_thresh":14.0,"density":2100,"use_phase3":true,
      "cost_gfrc":2.80,"cost_formwork":180.0}'

Stdout:  exactly ONE line of JSON with the full result payload.
Stderr:  all progress/debug messages (safe to log, never parsed).
"""

from __future__ import annotations
import json
import math
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

# ── stderr logger used everywhere ────────────────────────────────────────────
def _log(*a, **kw):
    print(*a, **kw, file=sys.stderr, flush=True)

def _mm2_to_m2(v): return v / 1_000_000
def _mm3_to_m3(v): return v / 1_000_000_000
def _kg_to_t(v):   return v / 1000


def main():
    if len(sys.argv) < 2:
        _emit_error("No filepath provided")
        return

    filepath = sys.argv[1]
    opts: dict = {}
    if len(sys.argv) >= 3:
        try:
            opts = json.loads(sys.argv[2])
        except Exception as e:
            _log(f"[warn] Could not parse options JSON: {e}, using defaults")

    gap_tol           = float(opts.get("gap_tol",           1.0))
    area_floor        = float(opts.get("area_floor",        1.0))
    return_depth      = float(opts.get("return_depth",      100.0))
    fiber_dosage      = float(opts.get("fiber_dosage",      0.05))
    complexity_thresh = float(opts.get("complexity_thresh", 14.0))
    density           = float(opts.get("density",           2100.0))
    use_phase3        = bool (opts.get("use_phase3",        True))
    cost_gfrc         = float(opts.get("cost_gfrc",         2.80))
    cost_formwork     = float(opts.get("cost_formwork",     180.0))

    suffix = os.path.splitext(filepath)[1].lower()

    # ── Phase 3 ───────────────────────────────────────────────────────────────
    issues_data = []
    groups = None

    if use_phase3:
        try:
            from gfrc_geometry import run_phase3_dxf, run_phase3_pdf
            if suffix == ".dxf":
                groups, issues = run_phase3_dxf(
                    filepath, gap_tolerance_mm=gap_tol, area_threshold_mm2=area_floor
                )
            else:
                groups, issues = run_phase3_pdf(
                    filepath, px_per_mm=1.0,
                    gap_tolerance_mm=gap_tol, area_threshold_mm2=area_floor
                )
            issues_data = [
                {"entity": i.entity_index, "layer": i.layer,
                 "type": i.issue_type, "detail": i.detail}
                for i in issues
            ]
            _log(f"[Phase 3] {len(issues_data)} issues logged")
        except ImportError as e:
            _log(f"[Phase 3] Shapely unavailable ({e}), falling back to Phase 1")
            use_phase3 = False

    # ── Phase 1 fallback ──────────────────────────────────────────────────────
    if groups is None:
        try:
            if suffix == ".dxf":
                from gfrc_quantifier import parse_dxf
                groups = parse_dxf(filepath)
            else:
                from gfrc_quantifier import parse_pdf, ScaleCalibration
                groups = parse_pdf(filepath, ScaleCalibration(1.0, 1.0))
        except Exception as e:
            _emit_error(f"Phase 1 parse failed: {e}")
            return

    # ── Phase 2 ───────────────────────────────────────────────────────────────
    try:
        from gfrc_engine import GFRCEngine, MaterialMapper
        mapper = MaterialMapper(
            default_thickness_mm = float(opts.get("default_thickness", 20.0)),
            density_kg_m3        = density,
        )
        engine = GFRCEngine(
            mapper               = mapper,
            fiber_dosage         = fiber_dosage,
            return_depth_mm      = return_depth,
            complexity_threshold = complexity_thresh,
        )
        results = engine.process(groups)
    except Exception as e:
        _emit_error(f"Phase 2 engine failed: {e}")
        return

    # ── Aggregate totals ──────────────────────────────────────────────────────
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

    # ── Build row data ────────────────────────────────────────────────────────
    rows = []
    for r in results:
        face_area_mm2 = r.return_area_mm2 if r.is_return else r.total_area_mm2
        face_area_m2  = _mm2_to_m2(face_area_mm2)
        vol_m3        = face_area_m2 * (r.thickness_mm / 1000)
        gfrc_cost     = r.panel_weight_kg * cost_gfrc
        fw_cost       = face_area_m2 * cost_formwork
        cf = r.complexity_factor if math.isfinite(r.complexity_factor) else None

        rows.append({
            "layer"           : r.layer,
            "entityType"      : r.entity_type,
            "classification"  : getattr(r, "classification", "Surface Panel"),
            "hexColor"        : r.hex_color,
            "thicknessMm"     : r.thickness_mm,
            "count"           : r.count,
            "perimeterMm"     : round(r.total_length_mm, 2),
            "faceAreaMm2"     : round(face_area_mm2, 2),
            "faceAreaM2"      : round(face_area_m2, 6),
            "volumeM3"        : round(vol_m3, 8),
            "weightKg"        : round(r.panel_weight_kg, 4),
            "fiberKg"         : round(r.fiber_weight_kg, 4),
            "isReturn"        : r.is_return,
            "returnAreaMm2"   : round(r.return_area_mm2, 2),
            "complexityFactor": round(cf, 4) if cf is not None else None,
            "complexityFlag"  : r.complexity_flag,
            "matchReason"     : r.match_reason,
            "gfrcCost"        : round(gfrc_cost, 2),
            "formworkCost"    : round(fw_cost, 2),
            "totalCost"       : round(gfrc_cost + fw_cost, 2),
        })

    # ── Emit single JSON line to stdout ───────────────────────────────────────
    payload = {
        "ok": True,
        "filename"    : os.path.basename(filepath),
        "phase3Used"  : use_phase3,
        "summary": {
            "totalVolumeM3"   : round(total_vol_m3, 6),
            "totalAreaM2"     : round(total_area_m2, 4),
            "totalWeightKg"   : round(total_weight_kg, 3),
            "totalWeightT"    : round(_kg_to_t(total_weight_kg), 4),
            "totalFiberKg"    : round(total_fiber_kg, 3),
            "totalGfrcCost"   : round(total_gfrc_cost, 2),
            "totalFwCost"     : round(total_fw_cost, 2),
            "totalCost"       : round(total_gfrc_cost + total_fw_cost, 2),
            "highComplexCount": sum(1 for r in results if "HIGH" in r.complexity_flag),
            "panelCount"      : sum(r.count for r in results),
            "groupCount"      : len(results),
            "issueCount"      : len(issues_data),
            "gapsSnapped"     : sum(getattr(g, "gaps_snapped", 0) for g in groups),
            "overlaysMerged"  : sum(1 for g in groups if getattr(g, "merged_overlap", False)),
        },
        "rows"  : rows,
        "issues": issues_data,
    }
    print(json.dumps(payload), flush=True)   # ← ONLY stdout write


def _emit_error(msg: str):
    _log(f"[ERROR] {msg}")
    print(json.dumps({"ok": False, "error": msg}), flush=True)


if __name__ == "__main__":
    main()
