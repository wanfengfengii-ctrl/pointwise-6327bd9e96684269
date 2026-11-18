from collections import defaultdict
from dataclasses import dataclass

from terrain.contour.incidence import level_sources
from terrain.contour.sections import SectionKind
from terrain.field.binding import LevelKey
from terrain.mesh.input import Edge, Point


@dataclass(frozen=True, slots=True)
class ContourArc:
    points: tuple[Point, Point]
    faces: tuple[str, ...]
    edges: tuple[Edge, ...]


@dataclass(frozen=True, slots=True)
class ContourNode:
    point: Point
    arcs: tuple[int, ...]
    faces: tuple[str, ...]
    edges: tuple[Edge, ...]
    vertices: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContourResult:
    key: LevelKey
    arcs: tuple[ContourArc, ...]
    nodes: tuple[ContourNode, ...]


def canonicalize_arcs(contributions):
    faces = defaultdict(set)
    edges = defaultdict(set)
    for a, b, face, edge in contributions:
        if a == b:
            raise ValueError('zero-length contour arc')
        key = (a, b) if a < b else (b, a)
        faces[key].add(face)
        if edge is not None:
            edges[key].add(edge)
    return tuple(ContourArc(key, tuple(sorted(faces[key])), tuple(sorted(edges[key])))
                 for key in sorted(faces))


def assemble_contours(level):
    vertices = level.field.revision.incidence.vertices
    contributions = []
    endpoints = defaultdict(list)
    for section in level.sections:
        if section.kind == SectionKind.POINT:
            endpoints[section.hits[0][0]]
        elif section.kind == SectionKind.SEGMENT:
            a, b = (point for point, _ in section.hits)
            contributions.append((a, b, section.face, None))
        else:
            for edge in section.equal_edges:
                if edge not in level.interior_edges:
                    a, b = (vertices[ref].point for ref in edge)
                    contributions.append((a, b, section.face, edge))
    arcs = canonicalize_arcs(contributions)
    for index, arc in enumerate(arcs):
        for point in arc.points:
            endpoints[point].append(index)
    nodes = []
    for point, indices in sorted(endpoints.items()):
        source = level_sources(level, point)
        nodes.append(ContourNode(point, tuple(indices), source.faces, source.edges, source.vertices))
    return ContourResult(level.key, arcs, tuple(nodes))
