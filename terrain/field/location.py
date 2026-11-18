from dataclasses import dataclass
from fractions import Fraction

from terrain.field.affine import barycentric_weights


@dataclass(frozen=True, slots=True)
class FaceHit:
    face: str
    location: str
    weights: tuple[Fraction, Fraction, Fraction]


def classify_point(patch, point):
    weights = barycentric_weights(patch, point)
    if min(weights) < 0:
        return None
    location = ('interior', 'edge', 'vertex')[weights.count(0)]
    return FaceHit(patch.face, location, weights)


def locate_faces(field, point):
    for patch in field.patches:
        hit = classify_point(patch, point)
        if hit is not None:
            yield patch, hit
