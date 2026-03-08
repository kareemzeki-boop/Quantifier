"""
gfrc_engine.py  ─  Phase 2: GFRC Calculation Engine
════════════════════════════════════════════════════
Maps extracted geometry (EntityGroup objects from Phase 1) to physical
material properties and produces a fully-costed quantification report.

Key concepts
------------
  Material Mapping   – resolves thickness from layer name keywords or hex color
  Panel Weight       – Area × Thickness × Density
  Glass Fiber        – Total Weight × fiber_dosage (default 5 %)
  Return / Flange    – length × return_depth for "RETURN"-tagged layers
  Complexity Factor  – perimeter-to-√area ratio; flagged when above threshold
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import sys as _sys
def _log(*args, **kwargs):
    print(*args, **kwargs, file=_sys.stderr, flush=True)


# ── lazy tabulate import (same pattern as Phase 1) ───────────────────────────
try:
    from tabulate import tabulate as _tabulate
    def tabulate(rows, headers, tablefmt="fancy_grid", **kw):
        return _tabulate(rows, headers=headers, tablefmt=tablefmt, **kw)
except ImportError:
    def tabulate(rows, headers, **kw):
        col_w = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
                 for i, h in enumerate(headers)]
        sep = "+" + "+".join("-" * (w + 2) for w in col_w) + "+"
        def fmt(row):
            return "|" + "|".join(f" {str(v):<{col_w[i]}} " for i, v in enumerate(row)) + "|"
        lines = [sep, fmt(headers), sep] + [fmt(r) for r in rows] + [sep]
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS & DEFAULTS
# ═══════════════════════════════════════════════════════════════════════════════

GFRC_DENSITY_KG_M3: float = 2100.0   # kg/m³  (per spec)
DEFAULT_FIBER_DOSAGE: float = 0.05    # 5 % of panel weight
DEFAULT_RETURN_DEPTH_MM: float = 100.0
COMPLEXITY_THRESHOLD: float = 14.0   # dimensionless; see _complexity_factor()

# ── Red detection: any hex colour whose R channel dominates ──────────────────
_RED_HEX_PATTERN = re.compile(r"^#([A-Fa-f0-9]{2})([A-Fa-f0-9]{2})([A-Fa-f0-9]{2})$")

def _is_red(hex_color: str) -> bool:
    """True if the colour is 'red' – R channel > 180 and both G, B < 80."""
    m = _RED_HEX_PATTERN.match(hex_color)
    if not m:
        return False
    r, g, b = int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16)
    return r > 180 and g < 80 and b < 80


# ═══════════════════════════════════════════════════════════════════════════════
#  MATERIAL MAPPING
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MaterialSpec:
    """Physical specification resolved for one EntityGroup."""
    thickness_mm: float
    density_kg_m3: float = GFRC_DENSITY_KG_M3
    is_return: bool = False          # layer tagged as a Return/Flange line
    match_reason: str = ""           # human-readable explanation of how matched


# Default material mapping rules.
# Keys are plain strings that are searched (case-insensitive) inside the layer
# name.  The value is the thickness in mm.  Rules are evaluated in order;
# first match wins.
DEFAULT_THICKNESS_RULES: dict[str, float] = {
    "20mm": 20.0,
    "25mm": 25.0,
    "30mm": 30.0,
    "40mm": 40.0,
    "50mm": 50.0,
    "15mm": 15.0,
    "12mm": 12.0,
    "10mm": 10.0,
}

DEFAULT_THICKNESS_MM: float = 20.0   # fallback when no rule matches


class MaterialMapper:
    """
    Resolves a MaterialSpec for each EntityGroup using:
      1. Layer name keyword match   (e.g. "PANEL-20mm-EAST")
      2. Hex colour match           (e.g. red → 20 mm per spec)
      3. Return / Flange detection  (layer name contains "RETURN" or "FLANGE")
      4. Fallback default thickness

    Parameters
    ----------
    thickness_rules : dict mapping layer-name keyword → thickness (mm).
                      If None, DEFAULT_THICKNESS_RULES is used.
    color_rules     : dict mapping hex string → thickness (mm).
                      Supports '#FF0000' literals AND the special key 'RED'
                      which triggers _is_red() heuristic.
    default_thickness_mm : fallback thickness when no rule fires.
    """

    def __init__(
        self,
        thickness_rules: Optional[dict[str, float]] = None,
        color_rules: Optional[dict[str, float]] = None,
        default_thickness_mm: float = DEFAULT_THICKNESS_MM,
        density_kg_m3: float = GFRC_DENSITY_KG_M3,
    ):
        self.thickness_rules   = thickness_rules or dict(DEFAULT_THICKNESS_RULES)
        self.color_rules: dict[str, float] = color_rules or {
            "RED":     20.0,   # any "red" colour (heuristic) → 20 mm
            "#FF0000": 20.0,   # exact ACI red
        }
        self.default_thickness = default_thickness_mm
        self.density           = density_kg_m3

    # ------------------------------------------------------------------
    def resolve(self, layer: str, hex_color: str) -> MaterialSpec:
        layer_upper = layer.upper()

        # 1 ── Return / Flange detection ──────────────────────────────
        is_return = any(kw in layer_upper for kw in ("RETURN", "FLANGE", "RET_", "_RET"))

        # 2 ── Layer-name keyword thickness ───────────────────────────
        for keyword, thickness in self.thickness_rules.items():
            if keyword.upper() in layer_upper:
                return MaterialSpec(
                    thickness_mm=thickness,
                    density_kg_m3=self.density,
                    is_return=is_return,
                    match_reason=f"layer keyword '{keyword}'",
                )

        # 3 ── Colour-based thickness ──────────────────────────────────
        for color_key, thickness in self.color_rules.items():
            if color_key.upper() == "RED":
                if _is_red(hex_color):
                    return MaterialSpec(
                        thickness_mm=thickness,
                        density_kg_m3=self.density,
                        is_return=is_return,
                        match_reason="color heuristic (RED)",
                    )
            elif hex_color.upper() == color_key.upper():
                return MaterialSpec(
                    thickness_mm=thickness,
                    density_kg_m3=self.density,
                    is_return=is_return,
                    match_reason=f"exact color match '{color_key}'",
                )

        # 4 ── Default fallback ────────────────────────────────────────
        return MaterialSpec(
            thickness_mm=self.default_thickness,
            density_kg_m3=self.density,
            is_return=is_return,
            match_reason="default thickness",
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  PHYSICAL CALCULATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _mm2_to_m2(area_mm2: float) -> float:
    return area_mm2 / 1_000_000.0

def _mm3_to_m3(vol_mm3: float) -> float:
    return vol_mm3 / 1_000_000_000.0

def _complexity_factor(perimeter_mm: float, area_mm2: float) -> float:
    """
    Dimensionless complexity ratio = Perimeter / sqrt(Area).

    Interpretation
    ──────────────
    A perfect circle gives  2π√π ≈ 11.2  (minimum possible for any shape).
    A square gives          4√4  = 8 … wait, let's recalculate correctly:
      square side s: perimeter=4s, area=s²  → ratio = 4s/s = 4  ← that's
      the standard isoperimetric quotient in a different form.

    We use perimeter / sqrt(area) which gives:
      • Circle   : 2πr / √(πr²) = 2√π ≈ 3.54
      • Square   : 4s  / s      = 4.0
      • Thin rect (10:1): 22s/√(10s²) ≈ 6.96
      • Very thin rect (100:1): ≈ 20.1  → HIGH COMPLEXITY

    Threshold COMPLEXITY_THRESHOLD = 14.0 catches highly elongated or
    jagged panels that require disproportionate formwork / casting effort.
    """
    if area_mm2 <= 0:
        return float("inf")
    return perimeter_mm / math.sqrt(area_mm2)


@dataclass
class GFRCResult:
    """All computed physical quantities for one EntityGroup."""
    # ── Geometry (from Phase 1) ────────────────────────────────────────
    layer: str
    entity_type: str
    hex_color: str
    lineweight: str
    count: int
    total_length_mm: float
    total_area_mm2: float

    # ── Material resolution ────────────────────────────────────────────
    thickness_mm: float
    match_reason: str
    is_return: bool

    # ── Calculated outputs ────────────────────────────────────────────
    return_area_mm2: float      # length × return_depth (only if is_return)
    panel_weight_kg: float      # area × thickness × density
    fiber_weight_kg: float      # panel_weight × dosage
    complexity_factor: float    # perimeter / √area
    complexity_flag: str        # "HIGH COMPLEXITY ⚠" or "Standard"

    # ── Return config echoed ──────────────────────────────────────────
    return_depth_mm: float


# ═══════════════════════════════════════════════════════════════════════════════
#  ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class GFRCEngine:
    """
    Applies material mapping and physical formulas to a list of EntityGroups.

    Parameters
    ----------
    mapper            : MaterialMapper instance (or None to use defaults).
    fiber_dosage      : glass fiber as fraction of panel weight (default 0.05).
    return_depth_mm   : depth used for return/flange area calculation (mm).
    complexity_threshold : perimeter/√area ratio above which a panel is
                          flagged HIGH COMPLEXITY.
    """

    def __init__(
        self,
        mapper: Optional[MaterialMapper] = None,
        fiber_dosage: float = DEFAULT_FIBER_DOSAGE,
        return_depth_mm: float = DEFAULT_RETURN_DEPTH_MM,
        complexity_threshold: float = COMPLEXITY_THRESHOLD,
    ):
        self.mapper               = mapper or MaterialMapper()
        self.fiber_dosage         = fiber_dosage
        self.return_depth_mm      = return_depth_mm
        self.complexity_threshold = complexity_threshold

    # ------------------------------------------------------------------
    def process(self, groups) -> list[GFRCResult]:
        """
        Accept a list of EntityGroup (from Phase 1 parse_dxf / parse_pdf)
        and return a list of GFRCResult with all physical quantities filled.
        """
        results: list[GFRCResult] = []

        for g in groups:
            spec = self.mapper.resolve(g.layer, g.hex_color)

            # ── Return / Flange area ──────────────────────────────────
            if spec.is_return:
                return_area_mm2 = g.total_length_mm * self.return_depth_mm
                face_area_mm2   = return_area_mm2   # for weight purposes
            else:
                return_area_mm2 = 0.0
                face_area_mm2   = g.total_area_mm2

            # ── Panel Weight ──────────────────────────────────────────
            # Area (mm²) × thickness (mm) = volume (mm³)
            volume_mm3   = face_area_mm2 * spec.thickness_mm
            volume_m3    = _mm3_to_m3(volume_mm3)
            weight_kg    = volume_m3 * spec.density_kg_m3

            # ── Glass Fiber Content ───────────────────────────────────
            fiber_kg = weight_kg * self.fiber_dosage

            # ── Complexity Factor ─────────────────────────────────────
            cf   = _complexity_factor(g.total_length_mm, face_area_mm2)
            flag = ("HIGH COMPLEXITY ⚠" if cf > self.complexity_threshold
                    else "Standard")

            results.append(GFRCResult(
                layer           = g.layer,
                entity_type     = g.entity_type,
                hex_color       = g.hex_color,
                lineweight      = g.lineweight,
                count           = g.count,
                total_length_mm = g.total_length_mm,
                total_area_mm2  = g.total_area_mm2,
                thickness_mm    = spec.thickness_mm,
                match_reason    = spec.match_reason,
                is_return       = spec.is_return,
                return_area_mm2 = return_area_mm2,
                panel_weight_kg = weight_kg,
                fiber_weight_kg = fiber_kg,
                complexity_factor = cf,
                complexity_flag   = flag,
                return_depth_mm   = self.return_depth_mm,
            ))

        return results


# ═══════════════════════════════════════════════════════════════════════════════
#  REPORT RENDERING
# ═══════════════════════════════════════════════════════════════════════════════

def print_phase2_report(results: list[GFRCResult], source_file: str):
    """
    Print three formatted tables to stdout:
      1. Geometry + Material  (layer, type, thickness, return)
      2. Weight + Fiber       (weight, fiber dosage)
      3. Complexity Analysis  (factor, flag)
    Also writes a combined CSV report.
    """
    if not results:
        _log("\n  [!] No results to report.")
        return

    stem = Path(source_file).stem
    div  = "═" * 100

    # ── Table 1 : Geometry & Material ────────────────────────────────────────
    _log(f"\n{div}")
    _log(f"  GFRC PHASE 2 REPORT  ─  {Path(source_file).name}")
    _log(f"  SECTION 1 / 3 : GEOMETRY & MATERIAL MAPPING")
    _log(div)

    h1 = ["Layer / Page", "Type", "Color", "Count",
          "Perimeter (mm)", "Face Area (mm²)", "Thickness (mm)",
          "Return?", "Ret. Area (mm²)", "Mapped by"]
    r1 = []
    for r in results:
        r1.append([
            r.layer, r.entity_type, r.hex_color, r.count,
            f"{r.total_length_mm:,.1f}",
            f"{r.total_area_mm2:,.1f}",
            f"{r.thickness_mm:.1f}",
            "YES" if r.is_return else "—",
            f"{r.return_area_mm2:,.1f}" if r.is_return else "—",
            r.match_reason,
        ])
    _log(tabulate(r1, headers=h1))

    # ── Table 2 : Weight & Fiber ──────────────────────────────────────────────
    _log(f"\n{div}")
    _log("  SECTION 2 / 3 : PANEL WEIGHT & GLASS FIBER CONTENT")
    _log(div)

    total_weight = sum(r.panel_weight_kg for r in results)
    total_fiber  = sum(r.fiber_weight_kg for r in results)

    h2 = ["Layer / Page", "Type", "Count",
          "Face Area (m²)", "Volume (m³)",
          "Density (kg/m³)", "Panel Weight (kg)", "Fiber @ 5% (kg)"]
    r2 = []
    for r in results:
        face_area_m2 = _mm2_to_m2(r.total_area_mm2 if not r.is_return
                                   else r.return_area_mm2)
        vol_m3 = face_area_m2 * (r.thickness_mm / 1000.0)
        r2.append([
            r.layer, r.entity_type, r.count,
            f"{face_area_m2:.4f}",
            f"{vol_m3:.6f}",
            f"{GFRC_DENSITY_KG_M3:.0f}",
            f"{r.panel_weight_kg:.3f}",
            f"{r.fiber_weight_kg:.3f}",
        ])
    # Totals
    r2.append(["── TOTAL ──", "", "", "", "",
                "", f"{total_weight:.3f}", f"{total_fiber:.3f}"])
    _log(tabulate(r2, headers=h2))

    # ── Table 3 : Complexity ─────────────────────────────────────────────────
    _log(f"\n{div}")
    _log("  SECTION 3 / 3 : COMPLEXITY ANALYSIS  (Perimeter / √Area)")
    _log(f"  HIGH COMPLEXITY threshold : factor > {COMPLEXITY_THRESHOLD}")
    _log(div)

    h3 = ["Layer / Page", "Type", "Count",
          "Perimeter (mm)", "Area (mm²)",
          "Complexity Factor", "Labor Flag"]
    r3 = []
    high_count = 0
    for r in results:
        cf_str = f"{r.complexity_factor:.2f}" if math.isfinite(r.complexity_factor) else "∞"
        r3.append([
            r.layer, r.entity_type, r.count,
            f"{r.total_length_mm:,.1f}",
            f"{r.total_area_mm2:,.1f}",
            cf_str,
            r.complexity_flag,
        ])
        if r.complexity_flag.startswith("HIGH"):
            high_count += 1
    _log(tabulate(r3, headers=h3))

    # ── Summary footer ────────────────────────────────────────────────────────
    _log(f"\n  {'─'*60}")
    _log(f"  OVERALL TOTALS")
    _log(f"  {'─'*60}")
    _log(f"  Total Panel Groups         : {len(results)}")
    _log(f"  Total Panel Count          : {sum(r.count for r in results)}")
    _log(f"  Total Face Area            : {_mm2_to_m2(sum(r.total_area_mm2 for r in results)):.4f} m²")
    _log(f"  Total Return Area          : {_mm2_to_m2(sum(r.return_area_mm2 for r in results)):.4f} m²")
    _log(f"  Total Panel Weight         : {total_weight:,.3f} kg")
    _log(f"  Total Glass Fiber (5%)     : {total_fiber:,.3f} kg")
    _log(f"  High Complexity Groups     : {high_count} / {len(results)}")
    _log(f"  {'─'*60}")

    # ── CSV export ────────────────────────────────────────────────────────────
    _write_csv(results, stem)


def _write_csv(results: list[GFRCResult], stem: str):
    """Write a single comprehensive CSV with all Phase 2 fields."""
    csv_path = f"{stem}_gfrc_phase2.csv"
    headers = [
        "Layer/Page", "EntityType", "HexColor", "Lineweight", "Count",
        "Perimeter_mm", "FaceArea_mm2", "Thickness_mm", "MatchReason",
        "IsReturn", "ReturnDepth_mm", "ReturnArea_mm2",
        "FaceArea_m2", "Volume_m3", "Density_kg_m3",
        "PanelWeight_kg", "FiberWeight_kg", "FiberDosage_pct",
        "ComplexityFactor", "ComplexityFlag",
    ]
    rows = []
    for r in results:
        face_area_mm2 = r.return_area_mm2 if r.is_return else r.total_area_mm2
        face_area_m2  = _mm2_to_m2(face_area_mm2)
        vol_m3        = face_area_m2 * (r.thickness_mm / 1000.0)
        cf_str        = f"{r.complexity_factor:.4f}" if math.isfinite(r.complexity_factor) else "inf"
        rows.append([
            r.layer, r.entity_type, r.hex_color, r.lineweight, r.count,
            f"{r.total_length_mm:.4f}", f"{r.total_area_mm2:.4f}",
            f"{r.thickness_mm:.2f}", r.match_reason,
            "YES" if r.is_return else "NO",
            f"{r.return_depth_mm:.2f}", f"{r.return_area_mm2:.4f}",
            f"{face_area_m2:.6f}", f"{vol_m3:.8f}",
            f"{GFRC_DENSITY_KG_M3:.1f}",
            f"{r.panel_weight_kg:.6f}", f"{r.fiber_weight_kg:.6f}",
            f"{DEFAULT_FIBER_DOSAGE*100:.1f}",
            cf_str, r.complexity_flag,
        ])

    with open(csv_path, "w", encoding="utf-8") as f:
        f.write(",".join(headers) + "\n")
        for row in rows:
            f.write(",".join(str(v) for v in row) + "\n")

    _log(f"\n  CSV saved → {csv_path}\n")


# ═══════════════════════════════════════════════════════════════════════════════
#  CONVENIENCE FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def run_phase2(
    groups,
    source_file: str,
    *,
    mapper: Optional[MaterialMapper] = None,
    fiber_dosage: float = DEFAULT_FIBER_DOSAGE,
    return_depth_mm: float = DEFAULT_RETURN_DEPTH_MM,
    complexity_threshold: float = COMPLEXITY_THRESHOLD,
) -> list[GFRCResult]:
    """
    One-call interface: takes Phase 1 EntityGroup list → prints Phase 2 report.

    Example
    -------
    from gfrc_quantifier import run
    from gfrc_engine import run_phase2, MaterialMapper

    groups  = run("facade.dxf")
    results = run_phase2(groups, "facade.dxf", return_depth_mm=120)
    """
    engine  = GFRCEngine(
        mapper               = mapper,
        fiber_dosage         = fiber_dosage,
        return_depth_mm      = return_depth_mm,
        complexity_threshold = complexity_threshold,
    )
    results = engine.process(groups)
    print_phase2_report(results, source_file)
    return results
