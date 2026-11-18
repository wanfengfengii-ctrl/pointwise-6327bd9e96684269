from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations

from terrain.band.boundaries import point_in_ring, signed_ring_area
from terrain.band.components import AreaLedger
from terrain.field.affine import evaluate_height
from terrain.field.binding import LevelKey, bind_field
from terrain.mesh.incidence import edge_key
from terrain.mesh.validation import cyclic_edges, orient2d, segment_intersection


@dataclass(frozen=True, slots=True)
class AuditIssue:
    code: str
    product: str
    detail: str


@dataclass(frozen=True, slots=True)
class AuditReport:
    issues: tuple[AuditIssue, ...]
    band_areas: tuple[tuple[str, Fraction], ...]
    shared_levels: tuple[LevelKey, ...]

    @property
    def ok(self):
        return not self.issues


def audit_bindings(view):
    revision = view.revision.revision_id
    for product in view.products:
        payload = product.payload
        keys = tuple(LevelKey(revision, level) for level in payload.levels)
        if payload.kind == 'contours':
            actual = tuple(contour.key for contour in payload.result)
        else:
            interval = payload.result.interval
            actual = interval.lower, interval.upper
        if (product.revision_id != revision or actual != keys or
                tuple(sorted(set(payload.levels))) != payload.levels or
                payload.kind not in ('contours', 'band')):
            yield AuditIssue('BINDING_MISMATCH', product.product_id, 'revision or exact level keys disagree')


def _level_contacts(patch, level):
    """Calculate source edge contacts directly from endpoint heights."""
    for i, j in ((0, 1), (1, 2), (2, 0)):
        p, q = patch.points[i], patch.points[j]
        a, b = patch.heights[i], patch.heights[j]
        edge = edge_key(patch.vertices[i], patch.vertices[j])
        if a == level:
            yield p, edge
        if b == level:
            yield q, edge
        if min(a, b) < level < max(a, b):
            point = tuple(((b - level) * x + (level - a) * y) / (b - a) for x, y in zip(p, q))
            yield point, edge


def _check_contour(field, contour):
    z = contour.key.value
    vertices = field.revision.incidence.vertices
    arc_faces = defaultdict(set)
    arc_edges = defaultdict(set)
    node_faces = defaultdict(set)
    node_edges = defaultdict(set)
    node_vertices = defaultdict(set)
    plateau_uses = Counter()
    isolated = set()
    for patch in field.patches:
        contacts = defaultdict(set)
        for point, edge in _level_contacts(patch, z):
            contacts[point].add(edge)
            node_faces[point].add(patch.face)
            node_edges[point].add(edge)
        for v, point, height in zip(patch.vertices, patch.points, patch.heights):
            if height == z:
                node_vertices[point].add(v)
        if all(height == z for height in patch.heights):
            for a, b in cyclic_edges(patch.vertices):
                edge = edge_key(a, b)
                p, q = sorted((vertices[a].point, vertices[b].point))
                arc_faces[p, q].add(patch.face)
                arc_edges[p, q].add(edge)
                plateau_uses[p, q] += 1
        elif len(contacts) == 2:
            p, q = sorted(contacts)
            arc_faces[p, q].add(patch.face)
            arc_edges[p, q].update(contacts[p] & contacts[q])
        elif len(contacts) == 1:
            isolated.update(contacts)
    keys = sorted(key for key in arc_faces if plateau_uses[key] < 2)
    expected = [(key, tuple(sorted(arc_faces[key])), tuple(sorted(arc_edges[key]))) for key in keys]
    actual = [(arc.points, arc.faces, arc.edges) for arc in contour.arcs]
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        return f'level {z}: missing arcs/sources {missing!r}; unexpected {unexpected!r}; actual count {len(actual)}'
    indices = defaultdict(list, {point: [] for point in isolated})
    for i, key in enumerate(keys):
        for point in key:
            indices[point].append(i)
    expected_nodes = [(p, tuple(indices[p]), tuple(sorted(node_faces[p])), tuple(sorted(node_edges[p])),
                       tuple(sorted(node_vertices[p]))) for p in sorted(indices)]
    actual_nodes = [(node.point, node.arcs, node.faces, node.edges, node.vertices) for node in contour.nodes]
    if actual_nodes != expected_nodes:
        return f'level {z}: contour node incidence differs from exact source contacts'
    return None


def _hull(points):
    order = sorted(points)
    if len(order) < 3:
        return ()
    def chain(sequence):
        result = []
        for point in sequence:
            while len(result) >= 2 and orient2d(result[-2], result[-1], point) <= 0:
                result.pop()
            result.append(point)
        return result[:-1]
    result = tuple(chain(order) + chain(reversed(order)))
    return result if len(result) >= 3 else ()


def _cell_outline(patch, lo, hi):
    if all(height == hi for height in patch.heights):
        return ()
    points = {point for point, height in zip(patch.points, patch.heights) if lo <= height <= hi}
    for bound in (lo, hi):
        points.update(point for point, _ in _level_contacts(patch, bound))
    return _hull(points)


def _line_ledger(edges):
    # Breakpoint counts compare oriented coverage even if a ring joined
    # multiple collinear sides into a single segment.
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


def _ring_simple(points):
    if len(points) < 3 or len(set(points)) != len(points):
        return False
    edges = tuple(cyclic_edges(points))
    for (i, (a, b)), (j, (c, d)) in combinations(enumerate(edges), 2):
        contacts = segment_intersection(a, b, c, d)
        shared = {a, b} & {c, d} if j == i + 1 or (i == 0 and j == len(edges) - 1) else set()
        if contacts != shared:
            return False
    return True


def _expected_groups(field, active, lo, hi):
    neighbors = defaultdict(set)
    incidence = field.revision.incidence
    for edge, uses in incidence.edges.items():
        if len(uses) != 2 or any(use.face not in active for use in uses):
            continue
        a, b = sorted(incidence.vertices[v].h for v in edge)
        crosses = lo <= a < hi if a == b else max(a, lo) < min(b, hi)
        if crosses:
            p, q = (use.face for use in uses)
            neighbors[p].add(q)
            neighbors[q].add(p)
    remaining = set(active)
    groups = set()
    while remaining:
        pending = [remaining.pop()]
        group = set(pending)
        while pending:
            next_faces = remaining & neighbors[pending.pop()]
            remaining.difference_update(next_faces)
            group.update(next_faces)
            pending.extend(next_faces)
        groups.add(frozenset(group))
    return groups


def _check_band(field, band):
    lo, hi = band.interval.lower.value, band.interval.upper.value
    planned = {patch.face: (patch, _cell_outline(patch, lo, hi)) for patch in field.patches}
    planned = {face: claim for face, claim in planned.items() if claim[1]}
    actual_faces = set()
    actual_groups = set()
    by_cell = by_ring = Fraction()
    for component in band.components:
        edges = Counter()
        cell_area = Fraction()
        for cell in component.cells:
            if cell.face not in planned or cell.face in actual_faces or len(cell.points) < 3:
                return f'[{lo},{hi}): unexpected, repeated or invalid cell {cell.face!r}', by_ring
            patch, expected = planned[cell.face]
            start = cell.points.index(min(cell.points))
            polygon = cell.points[start:] + cell.points[:start]
            flags = tuple(lo <= evaluate_height(patch, p) < hi for p in cell.points)
            if polygon != expected or cell.included != flags:
                return f'[{lo},{hi}): actual polygon or membership differs for {cell.face!r}', by_ring
            actual_faces.add(cell.face)
            edges.update(cyclic_edges(cell.points))
            cell_area += signed_ring_area(cell.points)
        actual_groups.add(frozenset(component.faces))
        rings = (component.outer, *component.holes)
        areas = tuple(signed_ring_area(ring.points) for ring in rings)
        by_ring += sum(areas)
        by_cell += cell_area
        if areas[0] <= 0 or any(area >= 0 for area in areas[1:]) or not all(_ring_simple(r.points) for r in rings):
            return 'invalid ring orientation or ring topology', by_ring
        if any(point_in_ring(p, component.outer.points) < 0 for hole in component.holes for p in hole.points):
            return 'hole is outside its reported component', by_ring
        boundary = ((a, b) for (a, b), count in edges.items() for _ in range(max(0, count - edges[b, a])))
        actual = (edge for ring in rings for edge in cyclic_edges(ring.points))
        if _line_ledger(boundary) != _line_ledger(actual):
            return 'actual oriented boundary differs from the component cell sides', by_ring
        if sum(areas) != cell_area or component.area != cell_area:
            return f'component area differs from actual rings or cells: {sum(areas)} / {cell_area}', by_ring
    if actual_faces != set(planned):
        return f'[{lo},{hi}): missing cells {sorted(set(planned) - actual_faces)!r}', by_ring
    if actual_groups != _expected_groups(field, planned, lo, hi):
        return f'[{lo},{hi}): actual open-interior groups disagree with shared-edge membership', by_ring
    if band.ledger != AreaLedger(by_cell, by_ring):
        return f'declared totals differ from actual cell and ring areas {by_cell} / {by_ring}', by_ring
    return None, by_ring


def audit_geometry(view):
    field = bind_field(view.revision)
    issues = []
    areas = []
    for product in view.products:
        payload = product.payload
        if payload.kind == 'contours':
            discrepancies = tuple(filter(None, (_check_contour(field, contour) for contour in payload.result)))
        else:
            issue, area = _check_band(field, payload.result)
            areas.append((product.product_id, area))
            discrepancies = (issue,) if issue else ()
        issues.extend(AuditIssue('GEOMETRY_MISMATCH', product.product_id, issue) for issue in discrepancies)
    return tuple(issues), tuple(areas)


def audit_sources(view):
    mesh = view.revision.incidence
    for product in view.products:
        payload = product.payload
        if payload.kind == 'contours':
            records = [record for contour in payload.result for record in (*contour.arcs, *contour.nodes)]
            unknown = any(set(record.faces).difference(mesh.face_vertices) or set(record.edges).difference(mesh.edges)
                          or set(getattr(record, 'vertices', ())).difference(mesh.vertices) for record in records)
        else:
            unknown = any(set(component.faces).difference(mesh.face_vertices) for component in payload.result.components)
        if unknown:
            yield AuditIssue('SOURCE_MISMATCH', product.product_id, 'source is absent from the fixed mesh')


def audit(view):
    if not view.products:
        return AuditReport((AuditIssue('BINDING_MISMATCH', '', 'delivery has no products'),), (), ())
    bindings = tuple(audit_bindings(view))
    if bindings:
        return AuditReport(bindings, (), ())
    geometry, areas = audit_geometry(view)
    contours = {z for p in view.products if p.payload.kind == 'contours' for z in p.payload.levels}
    bands = {z for p in view.products if p.payload.kind == 'band' for z in p.payload.levels}
    shared = tuple(LevelKey(view.revision.revision_id, level) for level in sorted(contours & bands))
    return AuditReport(geometry + tuple(audit_sources(view)), areas, shared)
