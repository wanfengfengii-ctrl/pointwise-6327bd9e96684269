from dataclasses import dataclass
from fractions import Fraction

from terrain.mesh.input import Point
from terrain.mesh.validation import orient2d


@dataclass(frozen=True, slots=True)
class AffinePatch:
    face: str
    vertices: tuple[str, str, str]
    points: tuple[Point, Point, Point]
    heights: tuple[Fraction, Fraction, Fraction]
    coefficients: tuple[Fraction, Fraction, Fraction]


def affine_patch(face, vertices):
    points = tuple(v.point for v in vertices)
    heights = tuple(v.h for v in vertices)
    p, q, r = points
    z, u, v = heights
    det = orient2d(p, q, r)
    a = ((u - z) * (r[1] - p[1]) - (v - z) * (q[1] - p[1])) / det
    b = ((q[0] - p[0]) * (v - z) - (r[0] - p[0]) * (u - z)) / det
    return AffinePatch(face, tuple(vertex.id for vertex in vertices), points, heights,
                       (a, b, z - a * p[0] - b * p[1]))


def evaluate_height(patch, point):
    a, b, c = patch.coefficients
    return a * point[0] + b * point[1] + c


def barycentric_weights(patch, point):
    a, b, c = patch.points
    det = orient2d(a, b, c)
    return (orient2d(b, c, point) / det, orient2d(c, a, point) / det,
            orient2d(a, b, point) / det)
