"""
gfrc_geometry.py  ─  Phase 3: Geometry Cleanup & Classification
════════════════════════════════════════════════════════════════
Sits between raw entity extraction (Phase 1) and the calculation engine
(Phase 2).  Takes a list of RawEntity objects (point lists + metadata),
applies Shapely-based cleaning, and returns CleanEntityGroup objects that
are drop-in replacements for the Phase 1 EntityGroup.

Pipeline per layer-group
────────────────────────
  1. Gap Snap      – if first/last vertex are within `gap_tolerance` mm,
                     snap them together so the ring closes cleanly.
  2. Validity Fix  – buffer(0) trick to auto-repair self-intersections.
  3. Planarity     – check whether Shapely reports the ring as a valid,
                     non-degenerate Polygon; if not → Linear Molding.
  4. Boolean Union – unary_union() across all Polygon geometries in the
                     same layer-group to merge overlaps / duplicates.
  5. Classification  Surface Panel  (valid closed polygon after union)
                     Linear Molding (open / non-planar / degenerate ring)

All lengths and areas use Shapely's exact geometric computation
(not the shoelace approximation used in Phase 1).

Dependencies
────────────
    pip install shapely
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Shapely import with friendly error ───────────────────────────────────────
try:
    from shapely.geometry import (
        LinearRing, LineString, MultiPolygon, Point, Polygon
    )
    from shapely.ops import unary_union
    from shapely.validation import explain_validity
    import shapely
    _SHAPELY_OK = True
except ImportError:
    _SHAPELY_OK = False

# ── Logging helpers ──────────────────────────────────────────────────────────
import sys as _sys
import json as _json

def _log(*args, **kwargs):
    """Print progress messages to stderr so stdout stays clean for JSON."""
    print(*args, **kwargs, file=_sys.stderr, flush=True)

def _emit_json(data: dict):
    """Write the single JSON result line to stdout."""
    print(_json.dumps(data), flush=True)

# ── lazy tabulate (same pattern across all modules) ──────────────────────────
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
        return "\n".join([sep, fmt(headers), sep] + [fmt(r) for r in rows] + [sep])


# ═══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_GAP_TOLERANCE_MM: float  = 1.0    # snap gap if endpoints < this distance
DEFAULT_AREA_THRESHOLD_MM2: float = 1.0   # polygons smaller than this are noise
MIN_VERTICES: int = 3                      # minimum vertices for a valid polygon


# ═══════════════════════════════════════════════════════════════════════════════
#  INPUT / OUTPUT DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RawEntity:
    """
    One entity coming out of the DXF/PDF parser (before Shapely processing).
    Carries the raw 2-D point list plus all metadata needed downstream.
    """
    pts: list[tuple[float, float]]   # ordered vertex list (may be open)
    layer: str
    hex_color: str
    lineweight: str
    entity_type: str                 # "LWPOLYLINE", "SPLINE", "PDF_PATH", …
    was_closed: bool = False         # True if the source entity had its closed flag set


@dataclass
class CleanIssue:
    """Records one cleanup action taken on a single entity."""
    entity_index: int
    layer: str
    issue_type: str      # "GAP_SNAPPED", "VALIDITY_REPAIRED", "DEGENERATE_DROPPED",
                         # "NON_PLANAR", "OVERLAP_MERGED"
    detail: str = ""


@dataclass
class CleanEntityGroup:
    """
    Post-cleanup aggregated group – API-compatible with Phase 1 EntityGroup
    so it feeds directly into GFRCEngine.process() unchanged.

    Extra Phase 3 fields capture cleanup diagnostics.
    """
    # ── Phase 1–compatible fields ──────────────────────────────────────────
    layer: str
    hex_color: str
    lineweight: str
    entity_type: str
    count: int = 0
    total_length_mm: float = 0.0
    total_area_mm2: float  = 0.0

    # ── Phase 3 additions ─────────────────────────────────────────────────
    classification: str = "Surface Panel"   # or "Linear Molding"
    raw_count: int = 0                      # entities before union/drop
    dropped_count: int = 0                  # degenerate / too-small entities
    merged_overlap: bool = False            # True if union reduced count
    gaps_snapped: int = 0                   # how many gaps were snapped shut
    repairs_applied: int = 0               # how many buffer(0) repairs
    issues: list[CleanIssue] = field(default_factory=list)

    # Geometry handle (not serialised to CSV)
    _shapely_geom: object = field(default=None, repr=False, compare=False)

    # Phase 1 compatible add() for non-Shapely fallback
    def add(self, length_mm: float, area_mm2: float):
        self.count += 1
        self.total_length_mm += length_mm
        self.total_area_mm2  += area_mm2


# ═══════════════════════════════════════════════════════════════════════════════
#  LOW-LEVEL GEOMETRY HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _gap_distance(pts: list[tuple[float, float]]) -> float:
    """Euclidean distance between first and last vertex."""
    if len(pts) < 2:
        return 0.0
    dx = pts[-1][0] - pts[0][0]
    dy = pts[-1][1] - pts[0][1]
    return math.hypot(dx, dy)


def _snap_close(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Return a copy of pts with the last vertex set equal to the first."""
    snapped = list(pts)
    snapped[-1] = snapped[0]
    return snapped


def _ring_length(geom) -> float:
    """Exterior ring perimeter of a Shapely Polygon."""
    return geom.exterior.length


def _build_polygon(pts: list[tuple[float, float]]) -> "Polygon | None":
    """
    Attempt to build a Shapely Polygon from a point list.
    Returns None if the ring is degenerate (< 3 unique pts, zero area).
    Applies buffer(0) self-intersection repair automatically.
    """
    if len(pts) < MIN_VERTICES:
        return None
    try:
        ring = LinearRing(pts)
        if not ring.is_simple:
            # Try to heal via buffer trick
            poly = Polygon(pts).buffer(0)
        else:
            poly = Polygon(pts)

        if poly.is_empty or poly.area < 1e-10:
            return None
        return poly
    except Exception:
        return None


def _poly_list(geom) -> "list[Polygon]":
    """Flatten a Polygon or MultiPolygon into a list of Polygons."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type == "MultiPolygon":
        return list(geom.geoms)
    return []


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN CLEANUP PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

class GeometryCleanup:
    """
    Processes a list of RawEntity objects through the full Phase 3 pipeline
    and returns CleanEntityGroup objects ready for Phase 2.

    Parameters
    ----------
    gap_tolerance_mm    : snap gap if endpoints are closer than this (mm).
    area_threshold_mm2  : discard polygons smaller than this (noise filter).
    """

    def __init__(
        self,
        gap_tolerance_mm:   float = DEFAULT_GAP_TOLERANCE_MM,
        area_threshold_mm2: float = DEFAULT_AREA_THRESHOLD_MM2,
    ):
        if not _SHAPELY_OK:
            raise ImportError(
                "Shapely is required for Phase 3.  Install with:\n"
                "    pip install shapely"
            )
        self.gap_tol    = gap_tolerance_mm
        self.area_floor = area_threshold_mm2

    # ── Public entry point ────────────────────────────────────────────────────

    def process(self, raw_entities: list[RawEntity]) -> tuple[
        list[CleanEntityGroup], list[CleanIssue]
    ]:
        """
        Run the full cleanup pipeline.

        Returns
        -------
        groups  : list of CleanEntityGroup (one per unique layer+color+lw key)
        issues  : flat list of every CleanIssue encountered (audit trail)
        """
        all_issues: list[CleanIssue] = []

        # ── Group raw entities by (layer, color, lineweight) ──────────────
        bucket: dict[tuple, list[tuple[int, RawEntity]]] = {}
        for idx, ent in enumerate(raw_entities):
            key = (ent.layer, ent.hex_color, ent.lineweight)
            bucket.setdefault(key, []).append((idx, ent))

        groups: list[CleanEntityGroup] = []

        for key, indexed_ents in bucket.items():
            layer, hex_color, lineweight = key

            # Collect the most common entity_type in this bucket
            type_counts: dict[str, int] = {}
            for _, e in indexed_ents:
                type_counts[e.entity_type] = type_counts.get(e.entity_type, 0) + 1
            dominant_type = max(type_counts, key=type_counts.__getitem__)

            cg = CleanEntityGroup(
                layer       = layer,
                hex_color   = hex_color,
                lineweight  = lineweight,
                entity_type = dominant_type,
                raw_count   = len(indexed_ents),
            )

            polys_for_union:  list["Polygon"] = []
            linear_lengths:   list[float]     = []

            for idx, ent in indexed_ents:
                pts = list(ent.pts)

                # ── Step 1 : Gap Snap ─────────────────────────────────────
                gap = _gap_distance(pts)
                snapped = False
                if 0 < gap <= self.gap_tol:
                    pts = _snap_close(pts)
                    snapped = True
                    cg.gaps_snapped += 1
                    issue = CleanIssue(
                        entity_index = idx,
                        layer        = layer,
                        issue_type   = "GAP_SNAPPED",
                        detail       = f"gap={gap:.4f}mm snapped to first vertex",
                    )
                    all_issues.append(issue)
                    cg.issues.append(issue)

                # ── Step 2 : Build Polygon & Validity Repair ──────────────
                poly = _build_polygon(pts)

                if poly is not None and not poly.is_valid:
                    repaired = poly.buffer(0)
                    if not repaired.is_empty:
                        poly = repaired
                        cg.repairs_applied += 1
                        issue = CleanIssue(
                            entity_index = idx,
                            layer        = layer,
                            issue_type   = "VALIDITY_REPAIRED",
                            detail       = explain_validity(poly),
                        )
                        all_issues.append(issue)
                        cg.issues.append(issue)

                # ── Step 3 : Planarity / Closure Check ───────────────────
                if poly is None or poly.is_empty or poly.area < self.area_floor:
                    # Not a valid closed surface → Linear Molding
                    ls = LineString(pts)
                    length = ls.length
                    linear_lengths.append(length)

                    if poly is not None and poly.area < self.area_floor:
                        issue_type = "DEGENERATE_DROPPED"
                        detail     = f"area={poly.area:.4f}mm² below floor {self.area_floor}mm²"
                    else:
                        issue_type = "NON_PLANAR"
                        detail     = "entity does not form a closed polygon ring"

                    issue = CleanIssue(
                        entity_index = idx,
                        layer        = layer,
                        issue_type   = issue_type,
                        detail       = detail,
                    )
                    all_issues.append(issue)
                    cg.issues.append(issue)
                    cg.dropped_count += 1
                else:
                    polys_for_union.append(poly)

            # ── Step 4 : Boolean Union ─────────────────────────────────────
            if polys_for_union:
                pre_union_count = len(polys_for_union)
                merged = unary_union(polys_for_union)

                post_polys = _poly_list(merged)
                post_count = len(post_polys)

                if post_count < pre_union_count:
                    cg.merged_overlap = True
                    issue = CleanIssue(
                        entity_index = -1,
                        layer        = layer,
                        issue_type   = "OVERLAP_MERGED",
                        detail       = (
                            f"{pre_union_count} polygons → {post_count} after "
                            f"unary_union ({pre_union_count - post_count} overlap(s) removed)"
                        ),
                    )
                    all_issues.append(issue)
                    cg.issues.append(issue)

                # ── Step 5 : Populate CleanEntityGroup ────────────────────
                cg.classification  = "Surface Panel"
                cg._shapely_geom   = merged
                cg.count           = post_count
                cg.total_area_mm2  = merged.area
                cg.total_length_mm = sum(p.exterior.length for p in post_polys)

            # Linear Molding fallback (all entities in this group were open)
            if linear_lengths:
                lin_group = CleanEntityGroup(
                    layer          = layer,
                    hex_color      = hex_color,
                    lineweight     = lineweight,
                    entity_type    = dominant_type,
                    count          = len(linear_lengths),
                    total_length_mm= sum(linear_lengths),
                    total_area_mm2 = 0.0,
                    classification = "Linear Molding",
                    raw_count      = len(linear_lengths),
                )
                groups.append(lin_group)

            if polys_for_union:
                groups.append(cg)

        groups.sort(key=lambda g: (g.layer, g.classification))
        return groups, all_issues


# ═══════════════════════════════════════════════════════════════════════════════
#  RAW ENTITY EXTRACTION  (wraps Phase 1 parser internals)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_raw_entities_dxf(filepath: str) -> list[RawEntity]:
    """
    Re-parse a DXF file, returning ALL polylines and splines (open + closed)
    as RawEntity objects so Phase 3 can decide closure, not Phase 1.
    """
    try:
        import ezdxf
    except ImportError:
        raise ImportError("pip install ezdxf")

    import math as _math

    doc = ezdxf.readfile(filepath)
    msp = doc.modelspace()
    entities: list[RawEntity] = []

    def _color_hex(entity):
        try:
            if entity.rgb is not None:
                r, g, b = entity.rgb
                return f"#{int(r):02X}{int(g):02X}{int(b):02X}"
        except AttributeError:
            pass
        aci = entity.dxf.get("color", None)
        if aci is None or aci == 256:
            layer_obj = doc.layers.get(entity.dxf.layer)
            if layer_obj:
                try:
                    if layer_obj.rgb is not None:
                        r, g, b = layer_obj.rgb
                        return f"#{int(r):02X}{int(g):02X}{int(b):02X}"
                except AttributeError:
                    pass
                aci = layer_obj.dxf.get("color", 7)
        ACI = {1:"#FF0000",2:"#FFFF00",3:"#00FF00",4:"#00FFFF",
               5:"#0000FF",6:"#FF00FF",7:"#FFFFFF",0:"#000000"}
        return ACI.get(aci or 7, f"ACI-{aci:03d}")

    def _lw(entity):
        lw = entity.dxf.get("lineweight", -1)
        return "DEFAULT" if lw < 0 else f"{lw/100:.2f}mm"

    for ent in msp:
        et = ent.dxftype()
        layer = ent.dxf.get("layer", "0")
        color = _color_hex(ent)
        lw    = _lw(ent)

        if et == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in ent.get_points()]
            was_closed = bool(ent.closed)
            if was_closed and pts and pts[0] != pts[-1]:
                pts = pts + [pts[0]]
        elif et == "POLYLINE":
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in ent.vertices]
            was_closed = bool(ent.is_closed)
            if was_closed and pts and pts[0] != pts[-1]:
                pts = pts + [pts[0]]
        elif et == "SPLINE":
            pts_raw = list(ent.control_points)
            if len(pts_raw) < 2:
                continue
            pts = [(p[0], p[1]) for p in pts_raw]
            was_closed = (bool(ent.closed) or
                          _math.hypot(pts[0][0]-pts[-1][0], pts[0][1]-pts[-1][1]) < 1e-6)
        elif et == "LINE":
            # Open line segments — always Linear Molding
            s = ent.dxf.start
            e = ent.dxf.end
            pts = [(s.x, s.y), (e.x, e.y)]
            was_closed = False
        else:
            continue

        if len(pts) < 2:
            continue

        entities.append(RawEntity(
            pts        = pts,
            layer      = layer,
            hex_color  = color,
            lineweight = lw,
            entity_type= et,
            was_closed = was_closed,
        ))

    return entities


def extract_raw_entities_pdf(filepath: str, px_per_mm: float) -> list[RawEntity]:
    """
    Re-parse a PDF file, returning all vector paths as RawEntity objects.
    px_per_mm comes from a ScaleCalibration.px_per_mm value.
    """
    try:
        import fitz
    except ImportError:
        raise ImportError("pip install pymupdf")

    import math as _math

    doc = fitz.open(filepath)
    entities: list[RawEntity] = []

    def _c2h(color):
        if not color or not isinstance(color, (list, tuple)) or len(color) < 3:
            return "#000000"
        r, g, b = [int(c * 255) for c in color[:3]]
        return f"#{r:02X}{g:02X}{b:02X}"

    def _path_pts(items):
        pts = []
        for item in items:
            if item[0] == "l":
                pts.append((item[1].x, item[1].y))
                pts.append((item[2].x, item[2].y))
            elif item[0] == "c":
                pts.append((item[1].x, item[1].y))
                pts.append((item[4].x, item[4].y))
            elif item[0] == "re":
                r2 = item[1]
                pts += [(r2.x0, r2.y0),(r2.x1, r2.y0),(r2.x1, r2.y1),(r2.x0, r2.y1)]
        return pts

    for page_num, page in enumerate(doc, start=1):
        layer = f"Page-{page_num:02d}"
        for drawing in page.get_drawings():
            items = drawing.get("items", [])
            if not items:
                continue
            pts_px = _path_pts(items)
            if len(pts_px) < 2:
                continue
            # Convert px → mm
            pts = [(x / px_per_mm, y / px_per_mm) for x, y in pts_px]
            stroke = drawing.get("color") or drawing.get("stroke_color")
            fill   = drawing.get("fill")
            color  = _c2h(stroke or fill)
            lw_px  = drawing.get("width", 1.0) or 1.0
            lw_mm  = lw_px / px_per_mm
            closed = (drawing.get("closePath", False) or
                      _math.hypot(pts_px[0][0]-pts_px[-1][0],
                                  pts_px[0][1]-pts_px[-1][1]) < 2.0)
            entities.append(RawEntity(
                pts        = pts,
                layer      = layer,
                hex_color  = color,
                lineweight = f"{lw_mm:.3f}mm",
                entity_type= "PDF_PATH",
                was_closed = closed,
            ))
    doc.close()
    return entities


# ═══════════════════════════════════════════════════════════════════════════════
#  REPORT RENDERING
# ═══════════════════════════════════════════════════════════════════════════════

def print_cleanup_report(
    groups: list[CleanEntityGroup],
    issues: list[CleanIssue],
    source_file: str,
):
    """Print Phase 3 cleanup summary and issue log."""
    stem = Path(source_file).stem
    div  = "═" * 100

    # ── Table 1 : Cleaned group summary ──────────────────────────────────────
    _log(f"\n{div}")
    _log(f"  GFRC PHASE 3 REPORT  ─  {Path(source_file).name}")
    _log(f"  SECTION 1 / 2 : CLEANED GEOMETRY GROUPS")
    _log(div)

    h1 = ["Layer", "Type", "Color", "Classification",
          "Raw→Clean", "Gaps Snapped", "Repairs", "Overlaps Merged",
          "Count", "Perimeter (mm)", "Area (mm²)"]
    r1 = []
    for g in groups:
        overlap = "YES ⚠" if g.merged_overlap else "—"
        r1.append([
            g.layer, g.entity_type, g.hex_color, g.classification,
            f"{g.raw_count}→{g.count + g.dropped_count}",
            g.gaps_snapped, g.repairs_applied, overlap,
            g.count,
            f"{g.total_length_mm:,.2f}",
            f"{g.total_area_mm2:,.2f}",
        ])
    _log(tabulate(r1, headers=h1))

    # Stats
    surface_panels  = [g for g in groups if g.classification == "Surface Panel"]
    linear_moldings = [g for g in groups if g.classification == "Linear Molding"]
    _log(f"\n  Surface Panels  : {len(surface_panels)} group(s)  │  "
          f"  Linear Moldings : {len(linear_moldings)} group(s)")
    _log(f"  Total Gaps Snapped : {sum(g.gaps_snapped for g in groups)}"
          f"   │  Total Repairs : {sum(g.repairs_applied for g in groups)}"
          f"   │  Groups with Merges : {sum(1 for g in groups if g.merged_overlap)}")

    # ── Table 2 : Issue log ───────────────────────────────────────────────────
    if issues:
        _log(f"\n{div}")
        _log("  SECTION 2 / 2 : CLEANUP ISSUE LOG")
        _log(div)

        # Group issues by type for a concise summary
        by_type: dict[str, list[CleanIssue]] = {}
        for iss in issues:
            by_type.setdefault(iss.issue_type, []).append(iss)

        h2 = ["Issue Type", "Count", "Affected Layers"]
        r2 = []
        for itype, iss_list in sorted(by_type.items()):
            layers = sorted({i.layer for i in iss_list})
            r2.append([itype, len(iss_list), ", ".join(layers)])
        _log(tabulate(r2, headers=h2))

        # Verbose log (first 30 issues to avoid flooding console)
        _log(f"\n  Verbose log (first 30 of {len(issues)}):")
        h3 = ["#", "Entity", "Layer", "Issue Type", "Detail"]
        r3 = []
        for i, iss in enumerate(issues[:30]):
            r3.append([i+1, iss.entity_index, iss.layer, iss.issue_type, iss.detail[:70]])
        _log(tabulate(r3, headers=h3))

    # ── CSV export ────────────────────────────────────────────────────────────
    _write_csv(groups, issues, stem)


def _write_csv(groups: list[CleanEntityGroup], issues: list[CleanIssue], stem: str):
    grp_path = f"{stem}_gfrc_phase3_groups.csv"
    iss_path = f"{stem}_gfrc_phase3_issues.csv"

    with open(grp_path, "w", encoding="utf-8") as f:
        hdr = ("Layer,EntityType,HexColor,Lineweight,Classification,"
               "RawCount,CleanCount,DroppedCount,GapsSnapped,Repairs,"
               "OverlapMerged,TotalLength_mm,TotalArea_mm2\n")
        f.write(hdr)
        for g in groups:
            f.write(",".join([
                g.layer, g.entity_type, g.hex_color, g.lineweight,
                g.classification, str(g.raw_count), str(g.count),
                str(g.dropped_count), str(g.gaps_snapped),
                str(g.repairs_applied), "YES" if g.merged_overlap else "NO",
                f"{g.total_length_mm:.4f}", f"{g.total_area_mm2:.4f}",
            ]) + "\n")
    _log(f"\n  Groups CSV  → {grp_path}")

    with open(iss_path, "w", encoding="utf-8") as f:
        f.write("EntityIndex,Layer,IssueType,Detail\n")
        for iss in issues:
            detail_safe = iss.detail.replace(",", ";").replace("\n", " ")
            f.write(f"{iss.entity_index},{iss.layer},{iss.issue_type},{detail_safe}\n")
    _log(f"  Issues CSV  → {iss_path}\n")


# ═══════════════════════════════════════════════════════════════════════════════
#  CONVENIENCE FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def run_phase3_dxf(
    filepath: str,
    gap_tolerance_mm:   float = DEFAULT_GAP_TOLERANCE_MM,
    area_threshold_mm2: float = DEFAULT_AREA_THRESHOLD_MM2,
) -> tuple[list[CleanEntityGroup], list[CleanIssue]]:
    """
    Full Phase 3 pipeline for a DXF file.

    Returns (groups, issues) where `groups` is compatible with
    GFRCEngine.process() from Phase 2.

    Example
    -------
    from gfrc_geometry import run_phase3_dxf, print_cleanup_report
    from gfrc_engine   import run_phase2

    groups, issues = run_phase3_dxf("facade.dxf", gap_tolerance_mm=0.5)
    print_cleanup_report(groups, issues, "facade.dxf")
    results = run_phase2(groups, "facade.dxf")
    """
    _log(f"[Phase 3] Extracting raw entities from DXF: {filepath}")
    raw = extract_raw_entities_dxf(filepath)
    _log(f"[Phase 3] {len(raw)} raw entities extracted")

    cleaner = GeometryCleanup(
        gap_tolerance_mm   = gap_tolerance_mm,
        area_threshold_mm2 = area_threshold_mm2,
    )
    groups, issues = cleaner.process(raw)

    surface  = sum(1 for g in groups if g.classification == "Surface Panel")
    linear   = sum(1 for g in groups if g.classification == "Linear Molding")
    _log(f"[Phase 3] Done: {surface} Surface Panel group(s), "
         f"{linear} Linear Molding group(s), {len(issues)} issue(s) logged")
    return groups, issues


def run_phase3_pdf(
    filepath: str,
    px_per_mm: float,
    gap_tolerance_mm:   float = DEFAULT_GAP_TOLERANCE_MM,
    area_threshold_mm2: float = DEFAULT_AREA_THRESHOLD_MM2,
) -> tuple[list[CleanEntityGroup], list[CleanIssue]]:
    """Full Phase 3 pipeline for a PDF file."""
    _log(f"[Phase 3] Extracting raw entities from PDF: {filepath}")
    raw = extract_raw_entities_pdf(filepath, px_per_mm)
    _log(f"[Phase 3] {len(raw)} raw entities extracted")

    cleaner = GeometryCleanup(
        gap_tolerance_mm   = gap_tolerance_mm,
        area_threshold_mm2 = area_threshold_mm2,
    )
    groups, issues = cleaner.process(raw)

    surface = sum(1 for g in groups if g.classification == "Surface Panel")
    linear  = sum(1 for g in groups if g.classification == "Linear Molding")
    _log(f"[Phase 3] Done: {surface} Surface Panel group(s), "
         f"{linear} Linear Molding group(s), {len(issues)} issue(s) logged")
    return groups, issues
