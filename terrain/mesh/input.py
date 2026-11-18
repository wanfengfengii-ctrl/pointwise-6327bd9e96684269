import hashlib
import json
import re
from dataclasses import dataclass
from fractions import Fraction


class MeshInputError(ValueError):
    pass


Point = tuple[Fraction, Fraction]
Edge = tuple[str, str]
_RATIONAL = re.compile(r'([+-]?[0-9]+)(?:/([+-]?[0-9]+))?')


def parse_rational(value):
    if isinstance(value, Fraction):
        return Fraction(value)
    if type(value) is int:
        return Fraction(value)
    match = _RATIONAL.fullmatch(value) if isinstance(value, str) else None
    if match:
        try:
            return Fraction(int(match[1]), int(match[2] or '1'))
        except (ValueError, ZeroDivisionError) as exc:
            raise MeshInputError(f'invalid rational {value!r}') from exc
    raise MeshInputError(f'expected an integer or exact fraction, got {value!r}')


def rational_text(value):
    return f'{value.numerator}/{value.denominator}'


def identity(value):
    if not isinstance(value, str) or not value or value.strip() != value or not value.isprintable():
        raise MeshInputError(f'invalid identity {value!r}')
    return value


@dataclass(frozen=True, slots=True)
class VertexInput:
    id: str
    x: Fraction
    y: Fraction
    h: Fraction

    @property
    def point(self):
        return self.x, self.y


@dataclass(frozen=True, slots=True)
class FaceInput:
    id: str
    vertices: tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class MeshInput:
    vertices: tuple[VertexInput, ...]
    faces: tuple[FaceInput, ...]


def _rows(values, name):
    if isinstance(values, (str, bytes, dict)):
        raise MeshInputError(f'{name} must be an iterable of rows')
    try:
        return iter(values)
    except TypeError as exc:
        raise MeshInputError(f'missing or invalid {name}') from exc


def _row(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise MeshInputError('expected a four-item vertex or face row')
    return value


def normalize_mesh_input(vertices, faces):
    vertex_rows = []
    for row in _rows(vertices, 'vertices'):
        key, x, y, h = _row(row)
        vertex_rows.append(VertexInput(identity(key), parse_rational(x), parse_rational(y), parse_rational(h)))
    face_rows = []
    for row in _rows(faces, 'faces'):
        key, *refs = _row(row)
        face_rows.append(FaceInput(identity(key), tuple(sorted(map(identity, refs)))))
    for kind, rows in (('vertex', vertex_rows), ('face', face_rows)):
        if len({row.id for row in rows}) != len(rows):
            raise MeshInputError(f'duplicate {kind} identity')
    return MeshInput(tuple(sorted(vertex_rows, key=lambda v: v.id)),
                     tuple(sorted(face_rows, key=lambda f: f.id)))


def mesh_document(mesh):
    return {'vertices': [[v.id, rational_text(v.x), rational_text(v.y), rational_text(v.h)]
                         for v in mesh.vertices],
            'faces': [[f.id, *f.vertices] for f in mesh.faces]}


def mesh_fingerprint(mesh):
    data = json.dumps(mesh_document(mesh), ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    return 'mesh-' + hashlib.sha256(data.encode('ascii')).hexdigest()
