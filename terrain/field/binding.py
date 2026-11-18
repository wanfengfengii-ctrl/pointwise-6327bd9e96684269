from dataclasses import dataclass
from fractions import Fraction

from terrain.field.affine import AffinePatch, affine_patch
from terrain.mesh.input import parse_rational
from terrain.mesh.revisions import MeshRevision


@dataclass(frozen=True, order=True, slots=True)
class LevelKey:
    revision_id: str
    value: Fraction


@dataclass(frozen=True, slots=True)
class BoundField:
    revision: MeshRevision
    patches: tuple[AffinePatch, ...]

    @property
    def revision_id(self):
        return self.revision.revision_id


def bind_field(revision):
    incidence = revision.incidence
    patches = tuple(affine_patch(face, tuple(incidence.vertices[v] for v in vertices))
                    for face, vertices in incidence.face_vertices.items())
    return BoundField(revision, patches)


def level_key(field, value):
    return LevelKey(field.revision_id, parse_rational(value))


def require_same_revision(revision_id, *bindings):
    if any(binding.revision_id != revision_id for binding in bindings):
        raise ValueError('bindings refer to different mesh revisions')
