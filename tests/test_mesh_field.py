import sqlite3
import unittest
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from terrain.archive.api import ProcessingRequestError, build_bands, build_contours
from terrain.archive.catalog import Catalog, UnknownProduct
from terrain.field.query import SampleInputError, sample
from terrain.mesh.input import MeshInputError, parse_rational
from terrain.mesh.revisions import MeshStoreError, RevisionConflict, UnknownRevision, put_mesh
from terrain.mesh.validation import MeshGeometryError


class TestMesh(unittest.TestCase):
    def test_affine_sample(self):
        with Catalog(':memory:') as cat:
            tri = put_mesh(cat.meshes, 'tri', [('a', 0, 0, 0), ('b', 2, 0, 2), ('c', 0, 2, 4)],
                           [('abc', 'a', 'b', 'c')])
            hit = sample(cat.meshes, tri.revision_id, '1/2', Fraction(1, 2))
            self.assertEqual((hit.status, hit.height, hit.faces), ('inside', Fraction(3, 2), ('abc',)))
            self.assertEqual(hit.hits[0].weights, (Fraction(1, 2), Fraction(1, 4), Fraction(1, 4)))
            quad = put_mesh(cat.meshes, 'quad', [('a', 0, 0, 0), ('b', 1, 0, 1), ('c', 1, 1, 3), ('d', 0, 1, 2)],
                            [('right', 'a', 'b', 'c'), ('left', 'd', 'c', 'a')])
            edge = sample(cat.meshes, quad.revision_id, '1/2', '1/2')
            self.assertEqual((edge.height, edge.faces), (Fraction(3, 2), ('left', 'right')))
            self.assertEqual(tuple(h.location for h in edge.hits), ('edge', 'edge'))
            vertex = sample(cat.meshes, quad.revision_id, 0, 0)
            self.assertEqual(vertex.faces, ('left', 'right'))
            self.assertEqual(tuple(h.location for h in vertex.hits), ('vertex', 'vertex'))
            empty = put_mesh(cat.meshes, 'empty', [], [])
            for revision, point in ((quad, (2, 2)), (empty, (0, 0))):
                outside = sample(cat.meshes, revision.revision_id, *point)
                self.assertEqual((outside.status, outside.height, outside.faces), ('outside', None, ()))
            self.assertEqual(build_contours(cat, 'empty-line', empty.revision_id, 1).payload.result[0].arcs, ())
            self.assertEqual(build_bands(cat, 'empty-band', empty.revision_id, 0, 3).payload.result.components, ())

    def test_invalid_and_repeated_requests(self):
        vertices = [['a', 0, 0, 0], ['b', 4, 0, '4/1'], ['c', 0, 4, 0]]
        faces = [['base', 'a', 'b', 'c']]
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'mesh.sqlite'
            with Catalog(path) as cat:
                revision = put_mesh(cat.meshes, 'survey', vertices, faces)
                vertices[1][3] = 999
                faces[0][2] = 'bad'
                for value in (None, True, 1.0, '1/0', 'NaN', '0.5'):
                    with self.subTest(value=value), self.assertRaises(MeshInputError):
                        put_mesh(cat.meshes, 'invalid', [('bad', value, 0, 0)], [])
                with self.assertRaises(MeshInputError):
                    put_mesh(cat.meshes, 'missing', None, [])
                self.assertEqual(parse_rational('-2/-4'), Fraction(1, 2))
                base = [('a', 0, 0, 0), ('b', 4, 0, 4), ('c', 0, 4, 0)]
                cases = [
                    ('bad-ref', base, [('bad', 'a', 'b', 'missing')]),
                    ('degenerate', base, [('bad', 'a', 'a', 'b')]),
                    ('identical', base, [('base', 'a', 'b', 'c'), ('copy', 'a', 'c', 'b')]),
                    ('contains', base + [('p', 1, 1, 1), ('q', 2, 1, 1), ('r', 1, 2, 1)],
                     [('base', 'a', 'b', 'c'), ('small', 'p', 'q', 'r')]),
                    ('crosses', base + [('p', 1, -1, 1), ('q', 2, 3, 1), ('r', 5, 1, 1)],
                     [('base', 'a', 'b', 'c'), ('cross', 'p', 'q', 'r')]),
                    ('tee', base + [('p', 2, 0, 2), ('q', 1, -1, 1), ('r', 3, -1, 3)],
                     [('base', 'a', 'b', 'c'), ('tee', 'p', 'q', 'r')]),
                ]
                for request, v, f in cases:
                    with self.subTest(request=request), self.assertRaises(MeshGeometryError):
                        put_mesh(cat.meshes, request, v, f)
                    self.assertIsNone(cat.meshes.find_request(request))
                self.assertEqual(cat.connection.execute('SELECT count(*) FROM mesh_revisions').fetchone(), (1,))
                self.assertEqual(sample(cat.meshes, revision.revision_id, 1, 1).height, 1)
                line = build_contours(cat, 'snapshot', revision.revision_id, 2)
                self.assertEqual(line.payload.result[0].arcs[0].points, ((2, 0), (2, 2)))
                with self.assertRaises(UnknownRevision):
                    sample(cat.meshes, 'unknown', 0, 0)
                with self.assertRaises(UnknownRevision):
                    build_bands(cat, 'missing', 'unknown', 0, 1)
                with self.assertRaises(SampleInputError):
                    sample(cat.meshes, revision.revision_id, 0.0, 0)
                for lo, hi in ((2, 1), (1, 1), (1, False)):
                    with self.assertRaises(ProcessingRequestError):
                        build_bands(cat, 'invalid', revision.revision_id, lo, hi)
                with self.assertRaises(ProcessingRequestError):
                    build_contours(cat, 'invalid', revision.revision_id, ['1/0'])
                with self.assertRaises(UnknownProduct):
                    cat.load_product('invalid')
            with Catalog(path) as cat:
                same = put_mesh(cat.meshes, 'survey', [('c', 0, 4, 0), ('b', '8/2', 0, 4), ('a', 0, 0, 0)],
                                [('base', 'c', 'b', 'a')])
                self.assertEqual(same, revision)
                with self.assertRaises(RevisionConflict):
                    put_mesh(cat.meshes, 'survey', [], [])
                self.assertEqual(sample(cat.meshes, same.revision_id, 1, 1).height, 1)

    def test_revision_publish_failure_and_vertex_contact(self):
        vertices = [('o', 0, 0, 0), ('a', 1, 0, 1), ('b', 0, 1, 1), ('c', -1, 0, 1), ('d', 0, -1, 1)]
        faces = [('one', 'o', 'a', 'b'), ('two', 'o', 'c', 'd')]
        with Catalog(':memory:') as cat:
            cat.connection.execute("CREATE TRIGGER reject_mesh BEFORE INSERT ON mesh_requests "
                                   "BEGIN SELECT RAISE(ABORT, 'survey storage failure'); END")
            with self.assertRaises(MeshStoreError) as failed:
                put_mesh(cat.meshes, 'survey', vertices, faces)
            self.assertIsInstance(failed.exception.__cause__, sqlite3.IntegrityError)
            self.assertEqual(cat.connection.execute('SELECT count(*) FROM mesh_revisions').fetchone(), (0,))
            self.assertIsNone(cat.meshes.find_request('survey'))
            cat.connection.execute('DROP TRIGGER reject_mesh')
            revision = put_mesh(cat.meshes, 'survey', vertices, faces)
            self.assertEqual(sample(cat.meshes, revision.revision_id, 0, 0).faces, ('one', 'two'))
            cat.initialize()
            self.assertEqual(cat.meshes.get_revision(revision.revision_id), revision)
