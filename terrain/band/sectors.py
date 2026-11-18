from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from terrain.band.clipping import BandCell
from terrain.mesh.validation import cyclic_edges


Side = tuple[str, int]


@dataclass(frozen=True, slots=True)
class BandSectorGraph:
    cells: Mapping[str, BandCell]
    twins: Mapping[Side, Side]
    neighbors: Mapping[str, frozenset[str]]


def build_band_sectors(field, cells, upper):
    if upper.field is not field:
        raise ValueError('band sectors have a different bound field')
    by_face = {cell.face: cell for cell in cells}
    mesh = field.revision.incidence
    shared_edges = {frozenset(use.face for use in uses): edge
                    for edge, uses in mesh.edges.items() if len(uses) == 2}
    neighbors = {face: set() for face in by_face}
    half_edges = {}
    twins = {}
    for cell in cells:
        for index, (a, b) in enumerate(cyclic_edges(cell.points)):
            side = cell.face, index
            if (a, b) in half_edges:
                raise ValueError('duplicate directed band side')
            other = half_edges.get((b, a))
            half_edges[a, b] = side
            if other is None:
                continue
            edge = shared_edges.get(frozenset((cell.face, other[0])))
            if edge is None or side in twins or other in twins:
                raise ValueError('band sides do not follow mesh topology')
            twins[side] = other
            twins[other] = side
            # Both cells have area. The side joins their interiors only if its
            # open segment belongs to the band, including at a flat lower edge.
            if edge not in upper.equal_edges:
                neighbors[cell.face].add(other[0])
                neighbors[other[0]].add(cell.face)
    return BandSectorGraph(MappingProxyType(by_face), MappingProxyType(twins),
                           MappingProxyType({face: frozenset(links) for face, links in neighbors.items()}))


def connected_cell_groups(graph):
    remaining = set(graph.cells)
    for root in sorted(graph.cells):
        if root not in remaining:
            continue
        remaining.remove(root)
        group = [root]
        pending = [root]
        while pending:
            adjacent = graph.neighbors[pending.pop()] & remaining
            remaining.difference_update(adjacent)
            pending.extend(adjacent)
            group.extend(adjacent)
        yield tuple(sorted(group))
