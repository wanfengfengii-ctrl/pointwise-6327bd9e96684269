"""Exact difference band between two independently triangulated surveys.

The two revisions share a plane coordinate system but not vertex or face
identities.  The difference field is piecewise affine on the overlay of the
two triangulations, so the band is obtained by intersecting every old face
with every new face and clipping the convex intersection by the exact
half-open interval.  Connectivity only follows a shared sub-edge whose
relative interior lies in the interval; an edge lying entirely on the upper
bound is a seam, and point contacts never merge components.
"""
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from functools import cmp_to_key

from terrain.band.boundaries import (BoundaryRing, _simple_cycles, canonical_ring, point_in_ring,
                                     signed_ring_area)
from terrain.band.clipping import polygon_area
from terrain.field.binding import bind_field
from terrain.mesh.input import Point
from terrain.mesh.validation import cyclic_edges, on_segment, orient2d


@dataclass(frozen=True, slots=True)
class ChangeComponent:
    outer: BoundaryRing
    holes: tuple[BoundaryRing, ...]
    area: Fraction
    contributions: tuple[tuple[str, str, Fraction], ...]


@dataclass(frozen=True, slots=True)
class ChangeBandResult:
    levels: tuple[Fraction, Fraction]
    components: tuple[ChangeComponent, ...]

    @property
    def area(self):
        return sum((component.area for component in self.components), Fraction())


@dataclass(frozen=True, slots=True)
class _Piece:
    old_face: str
    new_face: str
    points: tuple[Point, ...]
    gradient: tuple[Fraction, Fraction, Fraction]

    @property
    def area(self):
        return polygon_area(self.points)


def _difference(piece, point):
    a, b, c = piece.gradient
    return a * point[0] + b * point[1] + c


def _clean(points):
    """Drop duplicate and collinear vertices while keeping a convex order."""
    result = []
    for point in points:
        if not result or result[-1] != point:
            result.append(point)
    while len(result) > 1 and result[0] == result[-1]:
        result.pop()
    while len(result) >= 3:
        kept = [point for i, point in enumerate(result)
                if orient2d(result[i - 1], point, result[(i + 1) % len(result)]) != 0]
        if len(kept) == len(result):
            break
        if len(kept) < 3:
            return ()
        result = kept
    return tuple(result)


def _clip_halfplane(points, signed):
    """Keep the closed half-plane ``signed(point) >= 0`` as an exact polygon."""
    if not points:
        return ()
    output = []
    for point, other in cyclic_edges(points):
        here, there = signed(point), signed(other)
        if here >= 0:
            output.append(point)
        if (here > 0 and there < 0) or (here < 0 and there > 0):
            t = here / (here - there)
            output.append((point[0] + t * (other[0] - point[0]),
                           point[1] + t * (other[1] - point[1])))
    return _clean(output)


def _intersect_convex(subject, clip):
    output = tuple(subject)
    for a, b in cyclic_edges(clip):
        if not output:
            return ()
        output = _clip_halfplane(output, lambda point, a=a, b=b: orient2d(a, b, point))
    return output


def _band_pieces(old_field, new_field, lo, hi):
    pieces = []
    for old in old_field.patches:
        for new in new_field.patches:
            polygon = _intersect_convex(old.points, new.points)
            if len(polygon) < 3:
                continue
            a = new.coefficients[0] - old.coefficients[0]
            b = new.coefficients[1] - old.coefficients[1]
            c = new.coefficients[2] - old.coefficients[2]
            if a == 0 and b == 0:
                if not lo <= c < hi:
                    continue
                band = polygon
            else:
                band = _clip_halfplane(polygon, lambda point: a * point[0] + b * point[1] + c - lo)
                if len(band) < 3:
                    continue
                band = _clip_halfplane(band, lambda point: hi - (a * point[0] + b * point[1] + c))
                if len(band) < 3:
                    continue
            if polygon_area(band) <= 0:
                continue
            pieces.append(_Piece(old.face, new.face, band, (a, b, c)))
    return tuple(pieces)


def _edge_parameter(a, b, point):
    dx, dy = b[0] - a[0], b[1] - a[1]
    return ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx * dx + dy * dy)


def _split_segments(pieces):
    """Split every piece side at every overlay vertex lying on it."""
    nodes = set()
    for piece in pieces:
        nodes.update(piece.points)
    segments = defaultdict(list)
    for index, piece in enumerate(pieces):
        for start, end in cyclic_edges(piece.points):
            inside = sorted((_edge_parameter(start, end, point), point) for point in nodes
                            if point != start and point != end and on_segment(start, end, point))
            chain = [start, *(point for _, point in inside), end]
            for a, b in zip(chain, chain[1:]):
                segments[(a, b) if a < b else (b, a)].append((index, (a, b)))
    return segments


def _shared_connection(pieces, entries, lo, hi):
    """Whether the relative interior of the shared sub-edge has positive band length."""
    first = pieces[entries[0][0]]
    start, end = entries[0][1]
    d0, d1 = _difference(first, start), _difference(first, end)
    if d0 == d1:
        return lo <= d0 < hi
    low, high = sorted((d0, d1))
    if high < lo or low >= hi:
        return False
    return max(low, lo) < min(high, hi)


def _groups(pieces, segments, lo, hi):
    parent = list(range(len(pieces)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left, right):
        left, right = find(left), find(right)
        if left != right:
            parent[max(left, right)] = min(left, right)

    for entries in segments.values():
        if len(entries) > 2:
            raise ValueError('change band overlay has an over-covered shared side')
        if len(entries) != 2:
            continue
        (left, first), (right, second) = entries
        if left == right:
            raise ValueError('change band overlay repeats a piece side')
        if first == second:
            raise ValueError('change band overlay shares a side in one direction')
        if _shared_connection(pieces, entries, lo, hi):
            union(left, right)
    groups = defaultdict(list)
    for index in range(len(pieces)):
        groups[find(index)].append(index)
    return tuple(tuple(sorted(group)) for group in groups.values())


def _upper(direction):
    x, y = direction
    return y > 0 or (y == 0 and x >= 0)


def _compare_directions(left, right):
    if _upper(left) != _upper(right):
        return -1 if _upper(left) else 1
    cross = left[0] * right[1] - left[1] * right[0]
    if cross > 0:
        return -1
    if cross < 0:
        return 1
    return 0


def _next_halfedge(outgoing, incoming):
    """Follow the boundary keeping the component on the left.

    Among the outgoing sides at the end vertex choose the first one clockwise
    from the reverse of the incoming side.  This also separates loops that
    only meet at a single vertex.
    """
    start, end = incoming
    reverse = (start[0] - end[0], start[1] - end[1])
    directions = [(edge[1][0] - end[0], edge[1][1] - end[1]) for edge in outgoing]
    order = sorted(range(len(outgoing)),
                   key=cmp_to_key(lambda i, j: _compare_directions(directions[i], directions[j])))
    chosen = order[-1]
    for index in order:
        if _compare_directions(directions[index], reverse) >= 0:
            break
        chosen = index
    return outgoing[chosen]


def _trace_rings(halfedges):
    if not halfedges:
        return ()
    seen = set()
    outgoing = defaultdict(list)
    for edge in halfedges:
        if edge in seen:
            raise ValueError('change band boundary repeats a directed side')
        seen.add(edge)
        outgoing[edge[0]].append(edge)
    remaining = set(halfedges)
    rings = []
    for seed in sorted(halfedges):
        if seed not in remaining:
            continue
        walk = []
        current = seed
        while current in remaining:
            remaining.discard(current)
            walk.append(current[0])
            current = _next_halfedge(outgoing[current[1]], current)
        if current != seed:
            raise ValueError('change band boundary did not close on its own side')
        for cycle in _simple_cycles(tuple(walk)):
            if len(cycle) < 3 or signed_ring_area(cycle) == 0:
                continue
            rings.append(canonical_ring(cycle).points)
    return tuple(rings)


def _group_boundary(segments, group):
    members = set(group)
    boundary = []
    for entries in segments.values():
        inside = [(index, side) for index, side in entries if index in members]
        if len(inside) == 2:
            (left, first), (right, second) = inside
            if left != right and first != second:
                continue
        boundary.extend(side for _, side in inside)
    return tuple(boundary)


def _component(pieces, group, segments):
    rings = _trace_rings(_group_boundary(segments, group))
    exterior = [ring for ring in rings if signed_ring_area(ring) > 0]
    interiors = [ring for ring in rings if signed_ring_area(ring) < 0]
    if len(exterior) != 1:
        raise ValueError('change band component requires one positive exterior ring')
    outer = BoundaryRing(exterior[0])
    holes = tuple(BoundaryRing(ring) for ring in sorted(interiors))
    area = outer.area + sum((hole.area for hole in holes), Fraction())
    pieces_area = sum((pieces[index].area for index in group), Fraction())
    if area != pieces_area or area <= 0:
        raise ValueError('change band ring and piece areas disagree')
    for hole in holes:
        if any(point_in_ring(point, outer.points) < 0 for point in hole.points):
            raise ValueError('change band hole lies outside its component')
    ledger = defaultdict(Fraction)
    for index in group:
        piece = pieces[index]
        ledger[piece.old_face, piece.new_face] += piece.area
    contributions = tuple((old, new, value) for (old, new), value in sorted(ledger.items()) if value > 0)
    if sum((value for _, _, value in contributions), Fraction()) != area:
        raise ValueError('change band contributions disagree with component area')
    return ChangeComponent(outer, holes, area, contributions)


def change_result(old_revision, new_revision, lo, hi):
    """Compute the exact change band of two revisions over their common domain."""
    lo, hi = Fraction(lo), Fraction(hi)
    if lo >= hi:
        raise ValueError('change band requires lo < hi')
    old_field = bind_field(old_revision)
    new_field = bind_field(new_revision)
    pieces = _band_pieces(old_field, new_field, lo, hi)
    if not pieces:
        return ChangeBandResult((lo, hi), ())
    segments = _split_segments(pieces)
    components = [_component(pieces, group, segments) for group in _groups(pieces, segments, lo, hi)]
    components.sort(key=lambda component: (component.outer.points,
                                           tuple(ring.points for ring in component.holes)))
    return ChangeBandResult((lo, hi), tuple(components))
