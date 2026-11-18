import json
import unittest
from fractions import Fraction

from terrain.archive.api import build_bands, build_contours
from terrain.archive.catalog import Catalog
from terrain.delivery.audit import audit
from terrain.delivery.read import read_export
from terrain.mesh.revisions import put_mesh


def flat_square(*, slope=False):
    vertices = [('a', 0, 0, 1), ('b', 1, 0, 1), ('c', 1, 1, 1), ('d', 0, 1, 1)]
    faces = [('p0', 'a', 'b', 'c'), ('p1', 'a', 'c', 'd')]
    if slope:
        vertices.append(('e', 2, 0, 2))
        faces.append(('slope', 'b', 'e', 'c'))
    return vertices, faces


class TestContours(unittest.TestCase):
    def test_flat_square(self):
        vertices, faces = flat_square()
        expected_arcs = (((0, 0), (0, 1)), ((0, 0), (1, 0)),
                         ((0, 1), (1, 1)), ((1, 0), (1, 1)))
        with Catalog(':memory:') as cat:
            mesh = put_mesh(cat.meshes, 'square', vertices, faces)
            revision = mesh.revision_id
            lines = build_contours(cat, 'lines', revision, ['2/2', 1])
            below = build_bands(cat, 'below', revision, 0, 1).payload.result
            above = build_bands(cat, 'above', revision, 1, 2).payload.result
            contour, = lines.payload.result
            arcs = tuple(arc.points for arc in contour.arcs)
            actual = (arcs, below.area, above.area)
            expected = (expected_arcs, Fraction(0), Fraction(1))
            self.assertEqual(actual, expected, f'PLATEAU_TOPOLOGY_MISMATCH actual={actual!r} expected={expected!r}')
            self.assertEqual([(arc.faces, arc.edges) for arc in contour.arcs],
                             [(('p1',), (('a', 'd'),)), (('p0',), (('a', 'b'),)),
                              (('p1',), (('c', 'd'),)), (('p0',), (('b', 'c'),))])
            self.assertEqual((below.interval.upper, above.interval.lower), (contour.key, contour.key))
            self.assertEqual(above.components[0].faces, ('p0', 'p1'))
            view = read_export(cat, ['lines', 'below', 'above'])
            report = audit(view)
            self.assertTrue(report.ok, f'PLATEAU_TOPOLOGY_MISMATCH {report.issues!r}')
            self.assertEqual(report.shared_levels, (contour.key,))
            self.assertEqual(dict(report.band_areas), {'above': 1, 'below': 0})
            print('POINTWISE_SCENARIO ' + json.dumps({
                'case_id': 'V2.platform_boundary', 'input': {'vertices': vertices, 'faces': faces, 'level': 1},
                'expected': {'arcs': expected_arcs, 'areas': ['0', '1']},
                'actual': {'arcs': [[[int(x), int(y)] for x, y in edge] for edge in arcs],
                           'areas': [str(below.area), str(above.area)]},
                'downstream_expected': {'above': '1', 'below': '0'},
                'downstream_actual': {key: str(value) for key, value in report.band_areas},
                'code_paths': ['terrain/contour/plateaus.py', 'terrain/contour/assembly.py',
                               'terrain/band/clipping.py', 'terrain/delivery/audit.py'],
                'oracle': 'The four unit-square sides bound area one. The diagonal has two plateau uses.'
            }))

    def test_isolated_contact_and_exact_crossing(self):
        with Catalog(':memory:') as cat:
            revision = put_mesh(cat.meshes, 'tri', [('a', 0, 0, 0), ('b', 2, 0, 2), ('c', 0, 2, 2)],
                                [('f', 'a', 'b', 'c')])
            contacts, crossing = build_contours(cat, 'line', revision.revision_id, [0, '1/7']).payload.result
            self.assertEqual(contacts.arcs, ())
            self.assertEqual([(n.point, n.vertices, n.arcs) for n in contacts.nodes], [((0, 0), ('a',), ())])
            p = Fraction(1, 7)
            self.assertEqual(crossing.arcs[0].points, ((0, p), (p, 0)))
            self.assertEqual([node.arcs for node in crossing.nodes], [(0,), (0,)])
            self.assertTrue(audit(read_export(cat, 'line')).ok)
