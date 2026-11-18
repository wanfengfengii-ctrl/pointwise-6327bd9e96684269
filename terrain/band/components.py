from dataclasses import dataclass
from fractions import Fraction

from terrain.band.boundaries import BoundaryRing, nest_boundary_rings, trace_boundary_rings
from terrain.band.clipping import BandCell, BandInterval
from terrain.band.sectors import connected_cell_groups
from terrain.field.binding import require_same_revision


@dataclass(frozen=True, slots=True)
class BandComponent:
    outer: BoundaryRing
    holes: tuple[BoundaryRing, ...]
    area: Fraction
    cells: tuple[BandCell, ...]

    @property
    def faces(self):
        return tuple(cell.face for cell in self.cells)


@dataclass(frozen=True, slots=True)
class AreaLedger:
    cell_area: Fraction
    component_area: Fraction


@dataclass(frozen=True, slots=True)
class BandResult:
    interval: BandInterval
    components: tuple[BandComponent, ...]
    ledger: AreaLedger

    @property
    def area(self):
        return self.ledger.component_area


def assemble_band_result(interval, graph):
    components = []
    for group in connected_cell_groups(graph):
        outer, holes = nest_boundary_rings(trace_boundary_rings(graph, group))
        cells = tuple(graph.cells[face] for face in group)
        components.append(BandComponent(outer, holes, outer.area + sum(hole.area for hole in holes), cells))
    components.sort(key=lambda component: (component.outer.points,
                                           tuple(ring.points for ring in component.holes)))
    ledger = AreaLedger(sum((cell.area for cell in graph.cells.values()), Fraction()),
                        sum((component.area for component in components), Fraction()))
    result = BandResult(interval, tuple(components), ledger)
    validate_band_result(result)
    return result


def validate_band_result(result):
    interval = result.interval
    require_same_revision(interval.revision_id, interval.upper)
    if interval.lower.value >= interval.upper.value:
        raise ValueError('invalid band endpoints')
    by_cells = by_rings = Fraction()
    for component in result.components:
        outer, holes = nest_boundary_rings((component.outer, *component.holes))
        actual = outer.area + sum(hole.area for hole in holes)
        cell_area = sum((cell.area for cell in component.cells), Fraction())
        if actual <= 0 or actual != cell_area or actual != component.area:
            raise ValueError('band component cell and ring areas disagree')
        by_cells += cell_area
        by_rings += actual
    if result.ledger != AreaLedger(by_cells, by_rings):
        raise ValueError('band result area ledger disagrees with geometry')
