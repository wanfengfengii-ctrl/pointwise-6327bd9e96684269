from dataclasses import dataclass
from fractions import Fraction

from terrain.field.affine import evaluate_height
from terrain.field.binding import bind_field
from terrain.field.location import FaceHit, locate_faces
from terrain.mesh.input import MeshInputError, parse_rational


class SampleInputError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SampleResult:
    revision_id: str
    status: str
    height: Fraction | None
    hits: tuple[FaceHit, ...]

    @property
    def faces(self):
        return tuple(hit.face for hit in self.hits)


def sample(store, revision_id, x, y):
    try:
        point = parse_rational(x), parse_rational(y)
    except MeshInputError as exc:
        raise SampleInputError(str(exc)) from exc
    field = bind_field(store.get_revision(revision_id))
    hits = tuple(locate_faces(field, point))
    if not hits:
        return SampleResult(revision_id, 'outside', None, ())
    heights = {evaluate_height(patch, point) for patch, _ in hits}
    if len(heights) != 1:
        raise RuntimeError('inconsistent shared-point heights')
    return SampleResult(revision_id, 'inside', heights.pop(), tuple(hit for _, hit in hits))
