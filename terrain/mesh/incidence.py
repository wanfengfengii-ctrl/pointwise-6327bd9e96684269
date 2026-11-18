from collections import defaultdict
from dataclasses import dataclass
from functools import cmp_to_key
from types import MappingProxyType
from typing import Mapping

from terrain.mesh.input import Edge, VertexInput
from terrain.mesh.validation import cyclic_edges, orient2d


def edge_key(a, b):
    return (a, b) if a < b else (b, a)


@dataclass(frozen=True, slots=True)
class EdgeUse:
    face: str
    start: str
    end: str
    opposite: str


@dataclass(frozen=True, slots=True)
class MeshIncidence:
    vertices: Mapping[str, VertexInput]
    face_vertices: Mapping[str, tuple[str, str, str]]
    edges: Mapping[Edge, tuple[EdgeUse, ...]]
    vertex_faces: Mapping[str, tuple[str, ...]]


def build_mesh_incidence(mesh):
    vertices = {v.id: v for v in mesh.vertices}
    face_vertices = {}
    edges = defaultdict(list)
    vertex_faces = defaultdict(list)
    for face in mesh.faces:
        a, b, c = face.vertices
        if orient2d(vertices[a].point, vertices[b].point, vertices[c].point) < 0:
            b, c = c, b
        refs = (a, b, c)
        face_vertices[face.id] = refs
        for (start, end), opposite in zip(cyclic_edges(refs), (c, a, b)):
            edges[edge_key(start, end)].append(EdgeUse(face.id, start, end, opposite))
            vertex_faces[start].append(face.id)
    return MeshIncidence(MappingProxyType(vertices), MappingProxyType(face_vertices),
                         MappingProxyType({key: tuple(edges[key]) for key in sorted(edges)}),
                         MappingProxyType({key: tuple(vertex_faces[key]) for key in vertices}))


def ordered_vertex_fan(incidence, vertex):
    origin = incidence.vertices[vertex].point
    directions = {}
    for face in incidence.vertex_faces[vertex]:
        points = [incidence.vertices[v].point for v in incidence.face_vertices[face] if v != vertex]
        directions[face] = tuple(sum(p[i] - origin[i] for p in points) for i in (0, 1))

    def upper(vector):
        x, y = vector
        return y > 0 or (y == 0 and x >= 0)

    def compare(a, b):
        u, v = directions[a], directions[b]
        if upper(u) != upper(v):
            return -1 if upper(u) else 1
        cross = orient2d((0, 0), u, v)
        return -1 if cross > 0 else 1 if cross < 0 else (a > b) - (a < b)

    return tuple(sorted(directions, key=cmp_to_key(compare)))
