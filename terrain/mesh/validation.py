from itertools import combinations

from terrain.mesh.input import MeshInputError


class MeshGeometryError(MeshInputError):
    pass


def orient2d(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def cyclic_edges(points):
    return zip(points, points[1:] + points[:1])


def on_segment(a, b, p):
    return (orient2d(a, b, p) == 0 and
            all(min(x, y) <= z <= max(x, y) for x, y, z in zip(a, b, p)))


def segment_intersection(a, b, c, d):
    ab_c, ab_d = orient2d(a, b, c), orient2d(a, b, d)
    cd_a, cd_b = orient2d(c, d, a), orient2d(c, d, b)
    if ab_c * ab_d < 0 and cd_a * cd_b < 0:
        t = cd_a / (cd_a - cd_b)
        return {(a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))}
    return ({p for p in (a, b) if on_segment(c, d, p)} |
            {p for p in (c, d) if on_segment(a, b, p)})


def _inside(triangle, point):
    sides = [orient2d(a, b, point) for a, b in cyclic_edges(triangle)]
    return min(sides) >= 0 or max(sides) <= 0


def triangle_relation(left, right):
    """Return the extreme-point candidates of a closed triangle intersection."""
    for axis in (0, 1):
        a = [point[axis] for point in left]
        b = [point[axis] for point in right]
        if max(a) < min(b) or max(b) < min(a):
            return frozenset()
    contacts = {p for p in left if _inside(right, p)}
    contacts.update(p for p in right if _inside(left, p))
    for a, b in cyclic_edges(left):
        for c, d in cyclic_edges(right):
            contacts.update(segment_intersection(a, b, c, d))
    return frozenset(contacts)


def validate_mesh(mesh):
    vertices = {v.id: v.point for v in mesh.vertices}
    triangles = []
    for face in mesh.faces:
        try:
            points = tuple(vertices[ref] for ref in face.vertices)
        except KeyError as exc:
            raise MeshGeometryError(f'face {face.id!r} references unknown vertex {exc.args[0]!r}') from exc
        if orient2d(*points) == 0:
            raise MeshGeometryError(f'degenerate face {face.id!r}')
        triangles.append((face, points))
    for (left, a), (right, b) in combinations(triangles, 2):
        contacts = triangle_relation(a, b)
        if len(contacts) >= 3:
            p, q, *rest = sorted(contacts)
            if any(orient2d(p, q, r) for r in rest):
                raise MeshGeometryError(f'overlapping faces {left.id!r} and {right.id!r}')
        common = {vertices[ref] for ref in set(left.vertices).intersection(right.vertices)}
        if contacts != common:
            raise MeshGeometryError(f'nonconforming contact between {left.id!r} and {right.id!r}')
