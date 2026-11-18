from dataclasses import dataclass

from terrain.band.clipping import polygon_area
from terrain.mesh.input import Point
from terrain.mesh.validation import cyclic_edges, on_segment, orient2d


@dataclass(frozen=True, slots=True)
class BoundaryRing:
    points: tuple[Point, ...]

    @property
    def area(self):
        return signed_ring_area(self.points)


def signed_ring_area(points):
    return polygon_area(points)


def point_in_ring(point, ring):
    """Return 1 inside, 0 on the ring, or -1 outside."""
    x, y = point
    inside = False
    for a, b in cyclic_edges(ring):
        if on_segment(a, b, point):
            return 0
        if (a[1] > y) != (b[1] > y):
            cross_x = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x < cross_x:
                inside = not inside
    return 1 if inside else -1


def _simple_cycles(points):
    # Separate a weakly simple walk at repeated vertices. Each output keeps
    # its direction and therefore its signed contribution to the area.
    path = []
    indices = {}
    for point in points + points[:1]:
        if point in indices:
            start = indices[point]
            yield tuple(path[start:])
            for removed in path[start + 1:]:
                del indices[removed]
            del path[start + 1:]
        else:
            indices[point] = len(path)
            path.append(point)


def _canonical_ring(points):
    corners = tuple(p for i, p in enumerate(points)
                    if orient2d(points[i - 1], p, points[(i + 1) % len(points)]) != 0)
    start = corners.index(min(corners))
    return BoundaryRing(corners[start:] + corners[:start])


def trace_boundary_rings(graph, group):
    faces = set(group)
    def next_side(side):
        face, index = side
        return face, (index + 1) % len(graph.cells[face].points)

    def internal(side):
        twin = graph.twins.get(side)
        return twin is not None and twin[0] in faces

    outline = {(face, index) for face in faces for index in range(len(graph.cells[face].points))
               if not internal((face, index))}
    remaining = set(outline)
    rings = []
    for seed in sorted(outline):
        if seed not in remaining:
            continue
        walk = []
        current = seed
        while current in remaining:
            remaining.remove(current)
            face, index = current
            walk.append(graph.cells[face].points[index])
            current = next_side(current)
            crossed = set()
            while internal(current):
                if current in crossed:
                    raise ValueError('nonterminating band boundary at vertex')
                crossed.add(current)
                current = next_side(graph.twins[current])
        if current != seed:
            raise ValueError('band boundary did not close on its own side')
        for cycle in _simple_cycles(tuple(walk)):
            if len(cycle) >= 3 and signed_ring_area(cycle):
                rings.append(_canonical_ring(cycle))
    return tuple(sorted(rings, key=lambda ring: ring.points))


def nest_boundary_rings(rings):
    outer_rings = [ring for ring in rings if ring.area > 0]
    holes = tuple(ring for ring in rings if ring.area < 0)
    if len(outer_rings) != 1 or len(holes) + 1 != len(rings):
        raise ValueError('band component requires one positive exterior and negative holes')
    outer = outer_rings[0]
    if any(point_in_ring(point, outer.points) < 0 for hole in holes for point in hole.points):
        raise ValueError('hole lies outside its component')
    return outer, holes
