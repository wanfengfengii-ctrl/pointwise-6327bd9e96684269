import unittest
from fractions import Fraction

from terrain.archive.api import build_bands, build_contours
from terrain.archive.catalog import Catalog
from terrain.delivery.audit import audit
from terrain.delivery.read import read_export
from terrain.mesh.revisions import put_mesh
from tests.test_contours import flat_square


def ring_mesh():
    vertices = [(f'o{i}', *p, 1) for i, p in enumerate(((0, 0), (3, 0), (3, 3), (0, 3)))]
    vertices += [(f'i{i}', *p, 1) for i, p in enumerate(((1, 1), (2, 1), (2, 2), (1, 2)))]
    faces = []
    for i in range(4):
        j = (i + 1) % 4
        faces.extend(((f'a{i}', f'o{i}', f'o{j}', f'i{j}'), (f'b{i}', f'o{i}', f'i{j}', f'i{i}')))
    return vertices, faces


class TestBands(unittest.TestCase):
    def test_plateau_slope(self):
        with Catalog(':memory:') as cat:
            mesh = put_mesh(cat.meshes, 'slope', *flat_square(slope=True))
            revision = mesh.revision_id
            contour = build_contours(cat, 'lines', revision, 1).payload.result[0]
            shared, = [arc for arc in contour.arcs if arc.points == ((1, 0), (1, 1))]
            self.assertEqual((shared.faces, shared.edges), (('p0', 'slope'), (('b', 'c'),)))
            self.assertEqual(len(contour.arcs), 4)
            low = build_bands(cat, 'low', revision, 0, 1).payload.result
            high = build_bands(cat, 'high', revision, 1, 2).payload.result
            self.assertEqual((low.area, high.area), (0, Fraction(3, 2)))
            self.assertEqual(low.components, ())
            component, = high.components
            self.assertEqual(component.faces, ('p0', 'p1', 'slope'))
            self.assertEqual(component.outer.points, ((0, 0), (2, 0), (1, 1), (0, 1)))
            slope = component.cells[-1]
            self.assertEqual(dict(zip(slope.points, slope.included))[(2, 0)], False)
            report = audit(read_export(cat, ['lines', 'low', 'high']))
            self.assertTrue(report.ok, report.issues)
            self.assertEqual(report.shared_levels, (contour.key,))

    def test_vertex_saddle(self):
        vertices = [('o', 0, 0, 0), ('e', 1, 0, 1), ('n', 0, 1, -1),
                    ('w', -1, 0, 1), ('s', 0, -1, -1)]
        faces = [('ne', 'o', 'e', 'n'), ('nw', 'o', 'n', 'w'), ('sw', 'o', 'w', 's'), ('se', 'o', 's', 'e')]
        with Catalog(':memory:') as cat:
            mesh = put_mesh(cat.meshes, 'saddle', vertices, faces)
            revision = mesh.revision_id
            band = build_bands(cat, 'band', revision, 0, 2).payload.result
            self.assertEqual((band.area, len(band.components)), (1, 2))
            self.assertEqual({c.faces for c in band.components}, {('ne', 'se'), ('nw', 'sw')})
            self.assertEqual({c.area for c in band.components}, {Fraction(1, 2)})
            a, b = (set(c.outer.points) for c in band.components)
            self.assertEqual(a & b, {(0, 0)})
            contour = build_contours(cat, 'line', revision, 0).payload.result[0]
            node, = [n for n in contour.nodes if n.point == (0, 0)]
            self.assertEqual((len(node.arcs), node.faces), (4, ('ne', 'nw', 'se', 'sw')))
            reversed_mesh = put_mesh(cat.meshes, 'reverse', list(reversed(vertices)),
                                     [(face, c, b, a) for face, a, b, c in reversed(faces)])
            reverse = build_bands(cat, 'band-reversed', reversed_mesh.revision_id, 0, 2).payload.result
            self.assertEqual(reverse, band)
            report = audit(read_export(cat, ['band', 'line']))
            self.assertTrue(report.ok, report.issues)

    def test_ring_hole(self):
        vertices, faces = ring_mesh()
        with Catalog(':memory:') as cat:
            mesh = put_mesh(cat.meshes, 'ring', vertices, faces)
            revision = mesh.revision_id
            band = build_bands(cat, 'band', revision, 1, 2).payload.result
            component, = band.components
            hole, = component.holes
            self.assertEqual((band.area, component.outer.area, hole.area), (8, 9, -1))
            self.assertEqual(hole.points, ((1, 1), (1, 2), (2, 2), (2, 1)))
            self.assertEqual(component.faces, tuple(sorted(face[0] for face in faces)))
            self.assertTrue(audit(read_export(cat, 'band')).ok)

    def test_excluded_equal_edge_does_not_connect(self):
        vertices = [('a', 0, 0, 1), ('b', 0, 1, 1), ('l', -1, 0, 0), ('r', 1, 0, 0)]
        with Catalog(':memory:') as cat:
            mesh = put_mesh(cat.meshes, 'ridge', vertices, [('l', 'l', 'a', 'b'), ('r', 'r', 'b', 'a')])
            band = build_bands(cat, 'band', mesh.revision_id, 0, 1).payload.result
            self.assertEqual((band.area, len(band.components)), (1, 2))
            for component in band.components:
                cell, = component.cells
                self.assertEqual(sum(cell.included), 1)
            self.assertTrue(audit(read_export(cat, 'band')).ok)
