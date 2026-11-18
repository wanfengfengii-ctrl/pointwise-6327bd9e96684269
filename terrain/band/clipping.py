from dataclasses import dataclass
from fractions import Fraction

from terrain.contour.sections import interpolate_level
from terrain.field.binding import LevelKey, level_key
from terrain.mesh.input import Point
from terrain.mesh.validation import cyclic_edges


@dataclass(frozen=True, slots=True)
class BandInterval:
    lower: LevelKey
    upper: LevelKey

    @property
    def revision_id(self):
        return self.lower.revision_id

    def includes(self, value):
        return self.lower.value <= value < self.upper.value


@dataclass(frozen=True, slots=True)
class BandCell:
    face: str
    points: tuple[Point, ...]
    included: tuple[bool, ...]

    @property
    def area(self):
        return polygon_area(self.points)


def polygon_area(points):
    return sum((a[0] * b[1] - a[1] * b[0] for a, b in cyclic_edges(points)), Fraction()) / 2


def validate_interval(field, lo, hi):
    lower, upper = level_key(field, lo), level_key(field, hi)
    if lower.value >= upper.value:
        raise ValueError('band requires lo < hi')
    return BandInterval(lower, upper)


def _clip(vertices, value, *, lower):
    if not vertices:
        return ()
    output = []
    for (a, ha), (b, hb) in cyclic_edges(vertices):
        inside_a = ha >= value if lower else ha <= value
        inside_b = hb >= value if lower else hb <= value
        if inside_a != inside_b:
            output.append((interpolate_level(a, ha, b, hb, value), value))
        if inside_b:
            output.append((b, hb))
    return tuple(dict.fromkeys(output))


def clip_face_to_band(patch, interval, lower, upper):
    if patch.face in upper.plateau_faces:
        return None
    polygon = tuple(zip(patch.points, patch.heights))
    if patch.face not in lower.plateau_faces:
        polygon = _clip(polygon, interval.lower.value, lower=True)
        polygon = _clip(polygon, interval.upper.value, lower=False)
    points = tuple(point for point, _ in polygon)
    if len(points) < 3 or polygon_area(points) == 0:
        return None
    return BandCell(patch.face, points, tuple(interval.includes(height) for _, height in polygon))


def clip_band_cells(field, interval, lower, upper):
    if (lower.field is not field or upper.field is not field or
            (lower.key, upper.key) != (interval.lower, interval.upper)):
        raise ValueError('band endpoints have incompatible level incidence')
    cells = []
    for patch in field.patches:
        cell = clip_face_to_band(patch, interval, lower, upper)
        if cell is not None:
            cells.append(cell)
    return tuple(cells)
