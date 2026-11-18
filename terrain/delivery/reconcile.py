"""Independent reconciliation of a saved change band.

This module deliberately does not reuse the overlay/clipping code that built
the band.  It samples both archived fields with the original exact rational
point query on an interior lattice, derives the expected difference from the
two heights, and compares that with the saved rings and the saved
(old face, new face) contributions.  It also recomputes every ring area with
its own shoelace sum and checks ring orientation.

Run ``python3 -B -m terrain.delivery.reconcile`` to check a fixed set of
survey pairs; the process exits non-zero on any discrepancy.
"""
import argparse
import json
import sys
from fractions import Fraction

from terrain.archive.api import build_change_band
from terrain.archive.catalog import Catalog
from terrain.band import independent
from terrain.band.boundaries import point_in_ring
from terrain.field.binding import bind_field
from terrain.field.query import sample
from terrain.mesh.revisions import put_mesh


SQUARE = (("a", 0, 0), ("b", 1, 0), ("c", 1, 1), ("d", 0, 1))
AC = (("o0", "a", "b", "c"), ("o1", "a", "c", "d"))
BD = (("n0", "a", "b", "d"), ("n1", "b", "c", "d"))


def square(heights):
    return [(*point, height) for point, height in zip(SQUARE, heights, strict=True)]


def ring_mesh():
    vertices = [(f"o{i}", *p, 1) for i, p in enumerate(((0, 0), (3, 0), (3, 3), (0, 3)))]
    vertices += [(f"i{i}", *p, 1) for i, p in enumerate(((1, 1), (2, 1), (2, 2), (1, 2)))]
    faces = []
    for i in range(4):
        j = (i + 1) % 4
        faces.extend(((f"a{i}", f"o{i}", f"o{j}", f"i{j}"), (f"b{i}", f"o{i}", f"i{j}", f"i{i}")))
    return vertices, faces


def fan_mesh():
    vertices = [("A", 0, 0, 0), ("B", 1, 0, 0), ("C", 1, 1, 0), ("D", 0, 1, 0),
                ("M", Fraction(1, 2), Fraction(1, 2), 1)]
    faces = [("m1", "A", "B", "M"), ("m2", "B", "C", "M"), ("m3", "C", "D", "M"), ("m4", "D", "A", "M")]
    return vertices, faces


def flat(width):
    return ([("A", 0, 0, 0), ("B", width, 0, 0), ("C", width, width, 0), ("D", 0, width, 0)],
            [("q0", "A", "B", "C"), ("q1", "A", "C", "D")])


SCENARIOS = (
    ("lift", (square((0, 0, 0, 0)), AC), (square((1, 0, 0, 0)), BD),
     ((Fraction(1, 2), 2), (0, Fraction(1, 2)), (1, 2), (-2, 0))),
    ("ridge", (square((0, 0, 0, 0)), AC), (square((0, -1, 0, -1)), AC), ((-2, 0), (0, 1))),
    ("hole", ring_mesh(), flat(3), ((-1, 0), (-2, -1))),
    ("crack", flat(1), fan_mesh(), ((Fraction(1, 4), Fraction(1, 2)), (0, 1))),
    ("unchanged", (square((0, 0, 0, 0)), AC), (square((0, 0, 0, 0)), AC), ((0, 1), (1, 2))),
    ("zero-difference", (square((2, 2, 2, 2)), AC), (square((2, 2, 2, 2)), AC), ((2, 3), (0, 2))),
    ("empty", ([], []), (square((0, 0, 0, 0)), AC), ((0, 1),)),
    ("point-contact", (square((0, 0, 0, 0)), AC),
     ([("b", 1, 0, 0), ("e", 2, -1, 0), ("f", 2, 0, 0)], [("east", "b", "e", "f")]), ((0, 1),)),
    ("upper-seam", ([("l", -1, 0, 0), ("a", 0, 0, 0), ("b", 0, 1, 0), ("r", 1, 0, 0)],
                     [("lo", "l", "a", "b"), ("ro", "r", "b", "a")]),
     ([("l", -1, 0, 0), ("a", 0, 0, 1), ("b", 0, 1, 1), ("r", 1, 0, 0)],
      [("lo", "l", "a", "b"), ("ro", "r", "b", "a")]), ((0, 1),)),
)


def _area(points):
    return independent.area(points)


def _convex_intersection(left, right):
    return independent.convex_intersection(left, right)


def _band_polygon(convex, value):
    coefficients, low, high = value
    return independent.band_polygon(convex, coefficients, low, high)


def _hull(points):
    return independent.hull(points)


def expected_pair_areas(old_field, new_field, lo, hi):
    """Exact (old face, new face) -> area ledger, computed independently."""
    return independent.pair_areas(old_field, new_field, lo, hi)


def _lattice(revisions, divisions):
    xs = [vertex.point[0] for revision in revisions for vertex in revision.mesh.vertices]
    ys = [vertex.point[1] for revision in revisions for vertex in revision.mesh.vertices]
    if not xs or not ys:
        return ()
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    step_x, step_y = (x1 - x0) / divisions, (y1 - y0) / divisions
    return tuple((x0 + (i + Fraction(1, 2)) * step_x, y0 + (j + Fraction(1, 2)) * step_y)
                 for i in range(divisions) for j in range(divisions))


def _interior_face(store, revision_id, point):
    hit = sample(store, revision_id, point[0], point[1])
    if hit.status != "inside" or len(hit.hits) != 1 or hit.hits[0].location != "interior":
        return None
    return hit


def _containing(components, point, *, closure):
    """Components covering ``point``; ``closure`` also accepts ring boundaries."""
    found = []
    for component in components:
        relation = point_in_ring(point, component.outer.points)
        if relation == -1 or (relation == 0 and not closure):
            continue
        if any(point_in_ring(point, hole.points) == 1 for hole in component.holes):
            continue
        found.append(component)
    return found


def _strictly_inside(components, point):
    """Whether ``point`` lies in an open part of a component, not on any ring."""
    return any(
        point_in_ring(point, component.outer.points) == 1 and
        all(point_in_ring(point, hole.points) == -1 for hole in component.holes)
        for component in components)


def check_scenario(store, result, old_revision, new_revision, lo, hi, divisions):
    issues = []
    checked = 0
    combined = {}
    for component in result.components:
        for old, new, value in component.contributions:
            combined[old, new] = combined.get((old, new), Fraction()) + value
    expected = expected_pair_areas(bind_field(old_revision), bind_field(new_revision), lo, hi)
    if combined != expected:
        missing = sorted(set(expected.items()) - set(combined.items()))
        extra = sorted(set(combined.items()) - set(expected.items()))
        issues.append(f"per-pair ledger differs from independently intersected faces: "
                      f"missing {missing!r}, unexpected {extra!r}")
    for component in result.components:
        if _area(component.outer.points) <= 0:
            issues.append(f"{component.outer.points}: exterior ring is not counter-clockwise")
        for hole in component.holes:
            if _area(hole.points) >= 0:
                issues.append(f"{hole.points}: hole ring is not clockwise")
            if any(point_in_ring(point, component.outer.points) < 0 for point in hole.points):
                issues.append(f"{hole.points}: hole is outside its exterior")
        rings = _area(component.outer.points) + sum((_area(hole.points) for hole in component.holes),
                                                   Fraction())
        if rings != component.area:
            issues.append(f"{component.outer.points}: ring area {rings} differs from {component.area}")
        contributions = sum((value for _, _, value in component.contributions), Fraction())
        if contributions != component.area:
            issues.append(f"{component.outer.points}: contributions sum {contributions} "
                          f"differs from {component.area}")
        pairs = [(old, new) for old, new, _ in component.contributions]
        if len(set(pairs)) != len(pairs) or pairs != sorted(pairs):
            issues.append(f"{component.outer.points}: contribution pairs are repeated or unordered")
        if any(value <= 0 for _, _, value in component.contributions):
            issues.append(f"{component.outer.points}: non-positive contribution")
    total = sum((component.area for component in result.components), Fraction())
    if total != result.area:
        issues.append(f"declared total {result.area} differs from component sum {total}")

    for point in _lattice((old_revision, new_revision), divisions):
        old_hit = _interior_face(store, old_revision.revision_id, point)
        new_hit = _interior_face(store, new_revision.revision_id, point)
        if old_hit is None or new_hit is None:
            continue
        difference = new_hit.height - old_hit.height
        expected = lo <= difference < hi
        checked += 1
        if expected:
            # The band is closed by its area rings, so lattice points lying on a
            # ring are accepted; two closures may legitimately meet at a point.
            found = _containing(result.components, point, closure=True)
            if not found:
                issues.append(f"{point}: difference {difference} is in [{lo},{hi}) but "
                              "no component covers it")
                continue
            pair = old_hit.faces[0], new_hit.faces[0]
            if not any(pair in {(old, new) for old, new, _ in component.contributions}
                       for component in found):
                issues.append(f"{point}: expected contribution pair {pair} is missing from its component")
        else:
            # Outside the half-open interval a point may still lie on a ring,
            # since area rings represent the closed carrier of each component.
            if _strictly_inside(result.components, point):
                issues.append(f"{point}: difference {difference} is outside [{lo},{hi}) but is inside")
    return checked, issues


def exercise(divisions=6):
    report = {"scenarios": [], "points_checked": 0, "ok": True}
    with Catalog(":memory:") as catalog:
        for name, (old_mesh, old_faces), (new_mesh, new_faces), intervals in SCENARIOS:
            old = put_mesh(catalog.meshes, f"{name}-old", old_mesh, old_faces)
            new = put_mesh(catalog.meshes, f"{name}-new", new_mesh, new_faces)
            entry = {"scenario": name, "intervals": [], "issues": []}
            for index, (lo, hi) in enumerate(intervals):
                request = f"{name}-{index}"
                product = build_change_band(catalog, request, old.revision_id, new.revision_id, lo, hi)
                checked, issues = check_scenario(catalog.meshes, product.payload.result, old, new,
                                                 *product.payload.levels, divisions)
                entry["intervals"].append({"levels": [str(value) for value in product.payload.levels],
                                           "area": str(product.payload.result.area),
                                           "components": len(product.payload.result.components)})
                entry["issues"].extend(issues)
                report["points_checked"] += checked
            report["scenarios"].append(entry)
            report["ok"] = report["ok"] and not entry["issues"]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independently reconcile change bands by exact point queries")
    parser.add_argument("--divisions", type=int, default=6)
    args = parser.parse_args(argv)
    if args.divisions < 1:
        print("divisions must be positive", file=sys.stderr)
        return 2
    report = exercise(args.divisions)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
