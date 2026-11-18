from collections import defaultdict
from dataclasses import dataclass

from terrain.contour.sections import SectionKind
from terrain.mesh.incidence import ordered_vertex_fan
from terrain.mesh.input import Edge


@dataclass(frozen=True, slots=True)
class PlateauComponent:
    faces: tuple[str, ...]
    interior_edges: tuple[Edge, ...]
    boundary_edges: tuple[Edge, ...]
    attachments: tuple[tuple[str, tuple[str, ...]], ...]


def classify_plateau_edges(incidence, face_ids):
    interior = []
    boundary = []
    for edge, uses in incidence.edges.items():
        count = sum(use.face in face_ids for use in uses)
        if count == 2:
            interior.append(edge)
        elif count == 1:
            boundary.append(edge)
    return tuple(interior), tuple(boundary)


def collect_plateaus(incidence, sections):
    faces = {section.face for section in sections if section.kind == SectionKind.PLATEAU}
    adjacency = defaultdict(set)
    uses_by_face = defaultdict(set)
    interior, boundary = classify_plateau_edges(incidence, faces)
    for edge in interior:
        a, b = (use.face for use in incidence.edges[edge])
        adjacency[a].add(b)
        adjacency[b].add(a)
        uses_by_face[a].add(edge)
        uses_by_face[b].add(edge)
    for edge in boundary:
        owner = next(use.face for use in incidence.edges[edge] if use.face in faces)
        uses_by_face[owner].add(edge)
    interior_set = set(interior)
    remaining = set(faces)
    components = []
    for seed in sorted(faces):
        if seed not in remaining:
            continue
        remaining.remove(seed)
        pending = [seed]
        group = {seed}
        while pending:
            face = pending.pop()
            new = adjacency[face] & remaining
            remaining.difference_update(new)
            group.update(new)
            pending.extend(new)
        edges = set().union(*(uses_by_face[face] for face in group))
        outline = tuple(sorted(edges - interior_set))
        vertices = sorted({vertex for edge in outline for vertex in edge})
        attachments = tuple((v, tuple(f for f in ordered_vertex_fan(incidence, v) if f not in group))
                            for v in vertices)
        components.append(PlateauComponent(tuple(sorted(group)), tuple(sorted(edges & interior_set)),
                                           outline, attachments))
    return tuple(components)
