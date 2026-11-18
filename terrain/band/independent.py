"""Independent exact geometry for change bands.

This module derives the per-pair band areas from the two archived fields with
a different algorithm than :mod:`terrain.band.change`: convex polygons are
intersected by enumerating extreme points and hulling them (rather than
half-plane clipping), and the band part is obtained the same way.  It is used
by the offline audit and by the standalone reconciliation entry so that a
defect in the builder cannot hide behind a re-run of the same code.
"""
from collections import Counter, defaultdict
from fractions import Fraction


def area(points):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1])) / 2


def side(point, a, b):
    return (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])


def inside_convex(point, polygon):
    return all(side(point, a, b) >= 0 for a, b in zip(polygon, polygon[1:] + polygon[:1]))


def hull(points):
    order = sorted(set(points))
    if len(order) < 3:
        return ()

    def chain(sequence):
        result = []
        for point in sequence:
            while len(result) >= 2 and side(point, result[-2], result[-1]) <= 0:
                result.pop()
            result.append(point)
        return result[:-1]

    result = tuple(chain(order) + chain(order[::-1]))
    return result if len(result) >= 3 else ()


def convex_intersection(left, right):
    """Intersect two convex polygons from their extreme points and edge crossings."""
    candidates = [p for p in left if inside_convex(p, right)]
    candidates += [p for p in right if inside_convex(p, left)]
    for a, b in zip(left, left[1:] + left[:1]):
        for c, d in zip(right, right[1:] + right[:1]):
            r, s = (b[0] - a[0], b[1] - a[1]), (d[0] - c[0], d[1] - c[1])
            denominator = r[0] * s[1] - r[1] * s[0]
            if denominator == 0:
                continue
            t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / denominator
            u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / denominator
            if 0 <= t <= 1 and 0 <= u <= 1:
                candidates.append((a[0] + t * r[0], a[1] + t * r[1]))
    return hull(candidates)


def band_polygon(convex, coefficients, lo, hi):
    """Part of a convex polygon where ``a*x + b*y + c`` lies in ``[lo, hi)``."""
    a, b, c = coefficients

    def form(point):
        return a * point[0] + b * point[1] + c

    if all(form(point) == form(convex[0]) for point in convex):
        return convex if lo <= form(convex[0]) < hi else ()
    candidates = [p for p in convex if lo <= form(p) <= hi]
    for edge_a, edge_b in zip(convex, convex[1:] + convex[:1]):
        for level in (lo, hi):
            start, end = form(edge_a) - level, form(edge_b) - level
            if start == 0 or end == 0 or (start < 0) == (end < 0):
                continue
            t = start / (start - end)
            point = (edge_a[0] + t * (edge_b[0] - edge_a[0]),
                     edge_a[1] + t * (edge_b[1] - edge_a[1]))
            if lo <= form(point) <= hi:
                candidates.append(point)
    return hull(candidates)


def pair_areas(old_field, new_field, lo, hi):
    """Exact ``(old face, new face) -> area`` ledger for the change band."""
    ledger = {}
    for old, new, polygon, _ in pieces(old_field, new_field, lo, hi):
        ledger[old, new] = ledger.get((old, new), Fraction()) + area(polygon)
    return ledger


def pieces(old_field, new_field, lo, hi):
    """Yield ``(old face, new face, polygon)`` for every positive overlay piece."""
    lo, hi = Fraction(lo), Fraction(hi)
    result = []
    for old in old_field.patches:
        for new in new_field.patches:
            overlap = convex_intersection(old.points, new.points)
            if not overlap:
                continue
            coefficients = tuple(n - o for n, o in zip(new.coefficients, old.coefficients))
            band = band_polygon(overlap, coefficients, lo, hi)
            if band and area(band) > 0:
                result.append((old.face, new.face, band, coefficients))
    return result


def _shared_positive_segment(left, right):
    """Endpoints of a collinear positive-length overlap of two polygon sides, else None."""
    for a, b in zip(left, left[1:] + left[:1]):
        for c, d in zip(right, right[1:] + right[:1]):
            if side(a, c, d) != 0 or side(b, c, d) != 0:
                continue
            dx, dy = d[0] - c[0], d[1] - c[1]
            denominator = dx * dx + dy * dy
            if denominator == 0:
                continue
            ta = ((a[0] - c[0]) * dx + (a[1] - c[1]) * dy) / denominator
            tb = ((b[0] - c[0]) * dx + (b[1] - c[1]) * dy) / denominator
            start, end = max(0, min(ta, tb)), min(1, max(ta, tb))
            if start < end:
                return ((c[0] + start * dx, c[1] + start * dy),
                        (c[0] + end * dx, c[1] + end * dy))
    return None


def _form(coefficients, point):
    a, b, c = coefficients
    return a * point[0] + b * point[1] + c


def _joinable(coefficients, start, end, lo, hi):
    """Whether a collinear shared side carries a positive-length band part."""
    d0, d1 = _form(coefficients, start), _form(coefficients, end)
    if d0 == d1:
        return lo <= d0 < hi
    low, high = min(d0, d1), max(d0, d1)
    return max(low, lo) < min(high, hi)


def _directed_split(pieces):
    """Split every directed piece side at every overlay vertex lying on it."""
    nodes = set()
    for _, _, polygon, _ in pieces:
        nodes.update(polygon)
    segments = defaultdict(int)
    for _, _, polygon, _ in pieces:
        for start, end in zip(polygon, polygon[1:] + polygon[:1]):
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = dx * dx + dy * dy
            on_side = []
            for point in nodes:
                if point in (start, end):
                    continue
                if side(start, end, point) != 0:
                    continue
                t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length
                if 0 < t < 1:
                    on_side.append((t, point))
            chain = [start, *(point for _, point in sorted(on_side)), end]
            for a, b in zip(chain, chain[1:]):
                segments[a, b] += 1
    for a, b in list(segments):
        if (b, a) in segments and a < b:
            common = min(segments[a, b], segments[b, a])
            segments[a, b] -= common
            segments[b, a] -= common
    return tuple(segment for segment, count in segments.items() for _ in range(count))


def line_ledger(edges):
    """Breakpoint counts of oriented line coverage, ignoring collinear joins."""
    ledger = defaultdict(Counter)
    for a, b in edges:
        if a[0] == b[0]:
            line = (1, a[0])
            start, end = a[1], b[1]
        else:
            gradient = (b[1] - a[1]) / (b[0] - a[0])
            line = (0, gradient, a[1] - gradient * a[0])
            start, end = a[0], b[0]
        events = ledger[line, start < end]
        events[min(start, end)] += 1
        events[max(start, end)] -= 1
    return {line: {x: n for x, n in events.items() if n} for line, events in ledger.items() if any(events.values())}


def line_signature(edges):
    """Hashable canonical form of :func:`line_ledger`."""
    ledger = line_ledger(edges)
    return tuple(sorted((line, tuple(sorted(events.items()))) for line, events in ledger.items()))


def detailed_components(old_field, new_field, lo, hi):
    """Independent components with their oriented boundary edge multisets."""
    lo, hi = Fraction(lo), Fraction(hi)
    found = pieces(old_field, new_field, lo, hi)
    parent = list(range(len(found)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i in range(len(found)):
        for j in range(i + 1, len(found)):
            shared = _shared_positive_segment(found[i][2], found[j][2])
            if shared is None:
                continue
            if _joinable(found[i][3], *shared, lo, hi) and _joinable(found[j][3], *shared, lo, hi):
                left, right = find(i), find(j)
                if left != right:
                    parent[max(left, right)] = min(left, right)
    members = defaultdict(list)
    for index in range(len(found)):
        members[find(index)].append(index)
    rows = []
    for indices in members.values():
        group = [found[index] for index in indices]
        ledger = {}
        for old, new, polygon, _ in group:
            ledger[old, new] = ledger.get((old, new), Fraction()) + area(polygon)
        contributions = tuple((old, new, value) for (old, new), value in sorted(ledger.items()) if value > 0)
        total_area = sum((value for _, _, value in contributions), Fraction())
        rows.append((total_area, contributions, _directed_split(group)))
    return tuple(sorted(rows, key=lambda row: (row[0], row[1])))


def components(old_field, new_field, lo, hi):
    """Group overlay pieces by positive-length shared band sides."""
    return tuple((total_area, contributions) for total_area, contributions, _
                 in detailed_components(old_field, new_field, lo, hi))


def total(points_sequences):
    return sum((area(points) for points in points_sequences), Fraction())
