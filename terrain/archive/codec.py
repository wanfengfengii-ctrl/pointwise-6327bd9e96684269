import json
from dataclasses import dataclass
from fractions import Fraction

from terrain.band.boundaries import BoundaryRing
from terrain.band.clipping import BandCell, BandInterval
from terrain.band.components import AreaLedger, BandComponent, BandResult
from terrain.contour.assembly import ContourArc, ContourNode, ContourResult
from terrain.field.binding import LevelKey
from terrain.mesh.input import identity, parse_rational, rational_text
from terrain.mesh.revisions import restore_revision, revision_document


class CodecError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProductPayload:
    kind: str
    revision_id: str
    levels: tuple[Fraction, ...]
    result: tuple[ContourResult, ...] | BandResult


def dumps(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)


def encode_fraction(value):
    return rational_text(value)


def decode_fraction(value):
    try:
        number = parse_rational(value)
    except ValueError as exc:
        raise CodecError('invalid saved fraction') from exc
    if value != encode_fraction(number):
        raise CodecError('saved fraction must be canonical')
    return number


def _array(value, length=None):
    if not isinstance(value, list) or (length is not None and len(value) != length):
        raise CodecError('invalid array in saved payload')
    return value


def _ids(value):
    return tuple(map(identity, _array(value)))


def _edges(value):
    return tuple(tuple(map(identity, _array(edge, 2))) for edge in _array(value))


def _point(point):
    return [encode_fraction(x) for x in point]


def _points(points):
    return [_point(point) for point in points]


def _decode_point(point):
    return tuple(map(decode_fraction, _array(point, 2)))


def _decode_points(points):
    return tuple(map(_decode_point, _array(points)))


def encode_binding(kind, revision_id, levels):
    return {'kind': kind, 'revision': revision_id, 'levels': [encode_fraction(value) for value in levels]}


def encode_product(payload):
    binding = encode_binding(payload.kind, payload.revision_id, payload.levels)
    geometry, sources = [], []
    if payload.kind == 'contours':
        for contour in payload.result:
            geometry.append([encode_fraction(contour.key.value),
                             [_points(arc.points) for arc in contour.arcs],
                             [[_point(node.point), list(node.arcs)] for node in contour.nodes]])
            sources.append([[[list(arc.faces), [list(edge) for edge in arc.edges]] for arc in contour.arcs],
                            [[list(node.faces), [list(edge) for edge in node.edges], list(node.vertices)]
                             for node in contour.nodes]])
    elif payload.kind == 'band':
        result = payload.result
        for comp in result.components:
            geometry.append([_points(comp.outer.points), [_points(ring.points) for ring in comp.holes],
                             encode_fraction(comp.area),
                             [[_points(cell.points), list(cell.included)] for cell in comp.cells]])
            sources.append(list(comp.faces))
        geometry = [encode_fraction(result.ledger.cell_area), encode_fraction(result.area), geometry]
    else:
        raise CodecError(f'unknown product kind {payload.kind!r}')
    return binding, geometry, sources


def _decode_contour(revision, level, geometry, sources):
    declared, arc_points, node_points = _array(geometry, 3)
    if decode_fraction(declared) != level:
        raise CodecError('contour level does not match its binding')
    arc_sources, node_sources = _array(sources, 2)
    arcs = []
    for points, source in zip(_array(arc_points), _array(arc_sources), strict=True):
        faces, edges = _array(source, 2)
        arcs.append(ContourArc(_decode_points(_array(points, 2)), _ids(faces), _edges(edges)))
    nodes = []
    for row, source in zip(_array(node_points), _array(node_sources), strict=True):
        point, indices = _array(row, 2)
        faces, edges, vertices = _array(source, 3)
        indices = tuple(_array(indices))
        if any(type(index) is not int or not 0 <= index < len(arcs) for index in indices):
            raise CodecError('invalid contour node arc reference')
        nodes.append(ContourNode(_decode_point(point), indices, _ids(faces), _edges(edges), _ids(vertices)))
    return ContourResult(LevelKey(revision, level), tuple(arcs), tuple(nodes))


def _decode_band(interval, geometry, sources):
    by_cell, by_component, components = _array(geometry, 3)
    ledger = AreaLedger(decode_fraction(by_cell), decode_fraction(by_component))
    result = []
    for row, face_ids in zip(_array(components), _array(sources), strict=True):
        outer, holes, area, polygons = _array(row, 4)
        cells = []
        for polygon, face in zip(_array(polygons), _ids(face_ids), strict=True):
            coords, flags = _array(polygon, 2)
            points = _decode_points(coords)
            included = tuple(_array(flags, len(points)))
            if len(points) < 3 or any(type(flag) is not bool for flag in included):
                raise CodecError('invalid band cell structure')
            cells.append(BandCell(face, points, included))
        result.append(BandComponent(BoundaryRing(_decode_points(outer)),
                                    tuple(BoundaryRing(_decode_points(ring)) for ring in _array(holes)),
                                    decode_fraction(area), tuple(cells)))
    return BandResult(interval, tuple(result), ledger)


def decode_product(binding, geometry, sources):
    try:
        if not isinstance(binding, dict) or set(binding) != {'kind', 'revision', 'levels'}:
            raise CodecError('invalid product binding')
        kind = binding['kind']
        revision = identity(binding['revision'])
        levels = tuple(map(decode_fraction, _array(binding['levels'])))
        if tuple(sorted(set(levels))) != levels:
            raise CodecError('product levels must be strictly increasing')
        if kind == 'contours':
            result = tuple(_decode_contour(revision, level, geom, src)
                           for level, geom, src in zip(levels, _array(geometry), _array(sources), strict=True))
        elif kind == 'band' and len(levels) == 2:
            interval = BandInterval(*(LevelKey(revision, value) for value in levels))
            result = _decode_band(interval, geometry, sources)
        else:
            raise CodecError('invalid product kind or level count')
        return ProductPayload(kind, revision, levels, result)
    except CodecError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise CodecError('invalid saved product structure') from exc


def encode_mesh_snapshot(revision):
    return revision_document(revision)


def decode_mesh_snapshot(document):
    try:
        return restore_revision(document, verify_geometry=True)
    except (ValueError, KeyError, TypeError) as exc:
        raise CodecError('invalid mesh snapshot or revision binding') from exc
