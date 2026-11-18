from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from terrain.contour.plateaus import PlateauComponent, collect_plateaus
from terrain.contour.sections import FaceSection, SectionKind, section_face
from terrain.field.binding import BoundField, LevelKey, level_key, require_same_revision
from terrain.mesh.input import Edge, Point


@dataclass(frozen=True, slots=True)
class LevelPoint:
    faces: tuple[str, ...]
    edges: tuple[Edge, ...]
    vertices: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LevelIncidence:
    field: BoundField
    key: LevelKey
    sections: tuple[FaceSection, ...]
    plateaus: tuple[PlateauComponent, ...]
    plateau_faces: frozenset[str]
    interior_edges: frozenset[Edge]
    equal_edges: frozenset[Edge]
    points: Mapping[Point, LevelPoint]


def build_level_incidence(field, value):
    key = level_key(field, value)
    sections = tuple(section_face(patch, key) for patch in field.patches)
    plateaus = collect_plateaus(field.revision.incidence, sections)
    point_faces = defaultdict(set)
    point_edges = defaultdict(set)
    point_vertices = defaultdict(set)
    for section in sections:
        for point, edges in section.hits:
            point_faces[point].add(section.face)
            point_edges[point].update(edges)
        for point, vertex in section.vertices:
            point_vertices[point].add(vertex)
    points = {p: LevelPoint(tuple(sorted(point_faces[p])), tuple(sorted(point_edges[p])),
                            tuple(sorted(point_vertices[p]))) for p in sorted(point_faces)}
    result = LevelIncidence(field, key, sections, plateaus,
                            frozenset(f for component in plateaus for f in component.faces),
                            frozenset(e for component in plateaus for e in component.interior_edges),
                            frozenset(e for section in sections for e in section.equal_edges),
                            MappingProxyType(points))
    validate_level_incidence(result)
    return result


def validate_level_incidence(level):
    require_same_revision(level.field.revision_id, level.key)
    mesh = level.field.revision.incidence
    if (tuple(section.face for section in level.sections) != tuple(mesh.face_vertices) or
            any(section.key != level.key for section in level.sections)):
        raise ValueError('incomplete or mixed level sections')
    classified = {s.face: set(s.equal_edges) for s in level.sections if s.kind == SectionKind.PLATEAU}
    if set(classified) != level.plateau_faces:
        raise ValueError('platform faces disagree with sections')
    for component in level.plateaus:
        group = set(component.faces)
        outline = set(component.boundary_edges)
        interior = set(component.interior_edges)
        if outline & interior or outline | interior != set().union(*(classified[f] for f in group)):
            raise ValueError('incomplete platform edge association')
        for edge in outline | interior:
            n = sum(use.face in group for use in mesh.edges[edge])
            if n != (2 if edge in interior else 1):
                raise ValueError('incorrect platform edge incidence')
        for vertex, faces in component.attachments:
            point = mesh.vertices[vertex].point
            if set(faces) != set(level_sources(level, point).faces) - group:
                raise ValueError('incorrect platform vertex attachments')


def level_sources(level, point):
    return level.points[point]
