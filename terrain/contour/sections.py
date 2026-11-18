from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from terrain.field.binding import LevelKey
from terrain.mesh.incidence import edge_key
from terrain.mesh.input import Edge, Point


class SectionKind(StrEnum):
    EMPTY = 'empty'
    POINT = 'point'
    SEGMENT = 'segment'
    EDGE = 'edge'
    PLATEAU = 'plateau'


@dataclass(frozen=True, slots=True)
class FaceSection:
    key: LevelKey
    face: str
    kind: SectionKind
    hits: tuple[tuple[Point, tuple[Edge, ...]], ...]
    vertices: tuple[tuple[Point, str], ...]
    equal_edges: tuple[Edge, ...]


def interpolate_level(a, ha, b, hb, level):
    t = (level - ha) / (hb - ha)
    return a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])


def section_face(patch, key):
    value = key.value
    hits = defaultdict(set)
    equal_edges = []
    for i, j in ((0, 1), (1, 2), (2, 0)):
        a, b = patch.points[i], patch.points[j]
        ha, hb = patch.heights[i], patch.heights[j]
        edge = edge_key(patch.vertices[i], patch.vertices[j])
        if ha == value:
            hits[a].add(edge)
        if hb == value:
            hits[b].add(edge)
        if ha == hb == value:
            equal_edges.append(edge)
        elif min(ha, hb) < value < max(ha, hb):
            hits[interpolate_level(a, ha, b, hb, value)].add(edge)
    if len(equal_edges) == 3:
        kind = SectionKind.PLATEAU
    elif equal_edges:
        kind = SectionKind.EDGE
    else:
        kind = (SectionKind.EMPTY, SectionKind.POINT, SectionKind.SEGMENT)[len(hits)]
    vertices = tuple((point, ref) for point, ref, height in zip(patch.points, patch.vertices, patch.heights)
                     if height == value)
    return FaceSection(key, patch.face, kind,
                       tuple((p, tuple(sorted(edges))) for p, edges in sorted(hits.items())),
                       vertices, tuple(sorted(equal_edges)))
