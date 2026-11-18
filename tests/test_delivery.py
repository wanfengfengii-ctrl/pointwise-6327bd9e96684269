import json
import subprocess
import sys
import unittest
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from terrain.archive.api import build_bands, build_contours
from terrain.archive.catalog import Catalog
from terrain.band.boundaries import BoundaryRing
from terrain.delivery.audit import audit
from terrain.delivery.export import ExportError, build_package, validate_package
from terrain.delivery.read import read_export
from terrain.delivery.replay import load_package, replay_package
from terrain.mesh.revisions import put_mesh
from tests.test_archive import forbid_processing
from tests.test_bands import ring_mesh
from tests.test_contours import flat_square


_export_failure = None


def _fail_filesystem_operation(event, args):
    if _export_failure is None:
        return
    mode, destination = _export_failure
    if (mode == 'write' and event == 'tempfile.mkstemp' and
            Path(args[0]).parent == destination.parent and Path(args[0]).name.startswith(f'.{destination.name}.')):
        raise OSError('injected staging-file creation failure')
    if mode == 'replace' and event == 'os.rename' and Path(args[1]) == destination:
        raise OSError('injected atomic replacement failure')


class TestDelivery(unittest.TestCase):
    def test_frozen_reopen(self):
        denominator = 100000000000000000000000000000000001
        vertices = [('a', 0, 0, 0), ('b', '1/3', 0, '1/7'), ('c', 0, Fraction(1, denominator), 0)]
        faces = [('original-face', 'a', 'b', 'c')]
        with TemporaryDirectory() as tmp:
            db, package = Path(tmp) / 'terrain.sqlite', Path(tmp) / 'saved.json'
            with Catalog(db) as cat:
                old = put_mesh(cat.meshes, 'survey', vertices, faces)
                original = build_contours(cat, 'lines', old.revision_id, '1/21')
                band = build_bands(cat, 'band', old.revision_id, '1/21', '1/7')
                arc, = original.payload.result[0].arcs
                self.assertEqual(arc.points, ((Fraction(1, 9), 0), (Fraction(1, 9), Fraction(2, 3 * denominator))))
                self.assertEqual(arc.faces, ('original-face',))
                self.assertEqual(band.payload.result.area, Fraction(2, 27 * denominator))
                read_export(cat, ['lines', 'band'], package)
                first_bytes = package.read_bytes()
                updated = put_mesh(cat.meshes, 'corrected', [(v, x, y, 1) for v, x, y, _ in vertices],
                                   [('new-face', 'a', 'b', 'c')])
                self.assertNotEqual(updated.revision_id, old.revision_id)
            with Catalog(db) as reopened, forbid_processing():
                before = read_export(reopened, ['lines', 'band'], package)
                self.assertEqual(before.revision, old)
                self.assertEqual(before.products, (band, original))
                self.assertEqual(package.read_bytes(), first_bytes)
            db.unlink()
            with forbid_processing():
                view, report = replay_package(load_package(package))
            self.assertEqual(view, before)
            self.assertTrue(report.ok, report.issues)
            self.assertEqual(report.band_areas, (('band', Fraction(2, 27 * denominator)),))
            process = subprocess.run([sys.executable, '-B', '-m', 'terrain.delivery.replay', str(package)],
                                     check=False, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 0, process.stderr)
            offline = json.loads(process.stdout)
            self.assertEqual((offline['revision'], offline['products'], offline['band_areas']),
                             (old.revision_id, ['band', 'lines'], {'band': str(Fraction(2, 27 * denominator))}))
            self.assertFalse(db.exists())

    def test_export_failure_and_retriangulation(self):
        global _export_failure
        sys.addaudithook(_fail_filesystem_operation)
        with TemporaryDirectory() as tmp, Catalog(':memory:') as cat:
            directory = Path(tmp)
            path = directory / 'delivery.json'
            vertices, faces = flat_square()
            a = put_mesh(cat.meshes, 'old', vertices, faces)
            recode = {'a': 'w', 'b': 'z', 'c': 'u', 'd': 'v'}
            other_vertices = [(recode[v], x, y, h) for v, x, y, h in reversed(vertices)]
            b = put_mesh(cat.meshes, 'new', other_vertices, [('q1', 'z', 'u', 'v'), ('q0', 'v', 'z', 'w')])
            c0 = build_contours(cat, 'a-line', a.revision_id, 1).payload.result[0]
            c1 = build_contours(cat, 'b-line', b.revision_id, 1).payload.result[0]
            p0 = build_bands(cat, 'a-band', a.revision_id, 1, 2).payload.result
            p1 = build_bands(cat, 'b-band', b.revision_id, 1, 2).payload.result
            self.assertEqual([arc.points for arc in c0.arcs], [arc.points for arc in c1.arcs])
            self.assertEqual((p0.area, p1.area), (1, 1))
            self.assertEqual(p0.components[0].outer, p1.components[0].outer)
            self.assertEqual((p0.components[0].holes, p1.components[0].holes), ((), ()))
            self.assertEqual((p0.components[0].faces, p1.components[0].faces), (('p0', 'p1'), ('q0', 'q1')))
            view = read_export(cat, ['a-band', 'a-line'], path)
            old_bytes = path.read_bytes()
            b_view = read_export(cat, ['b-band', 'b-line'])
            self.assertTrue(audit(view).ok)
            self.assertTrue(audit(b_view).ok)
            for mode in ('write', 'replace'):
                for destination in (path, directory / 'first.json'):
                    with self.subTest(mode=mode, destination=destination.name):
                        _export_failure = mode, destination
                        try:
                            with self.assertRaises(ExportError) as error:
                                read_export(cat, ['b-band', 'b-line'], destination)
                            self.assertIn('injected', str(error.exception))
                        finally:
                            _export_failure = None
                        self.assertEqual(path.read_bytes(), old_bytes)
                        self.assertEqual(sorted(p.name for p in directory.iterdir()), ['delivery.json'])
            read_export(cat, ['b-band', 'b-line'], path)
            self.assertEqual(load_package(path).view, b_view)
            document = build_package(a, view.products)
            wrong_mesh = deepcopy(document)
            wrong_mesh['mesh'] = build_package(b, b_view.products)['mesh']
            with self.assertRaisesRegex(ExportError, 'binding'):
                validate_package(wrong_mesh)
            wrong_level = deepcopy(document)
            wrong_level['manifest']['entries'][0][2] = ['0/1', '2/1']
            with self.assertRaisesRegex(ExportError, 'manifest'):
                validate_package(wrong_level)
            mismatch = audit(replace(view, revision=b))
            self.assertFalse(mismatch.ok)
            self.assertEqual(mismatch.issues[0].code, 'BINDING_MISMATCH')

    def test_audit_actuals(self):
        with TemporaryDirectory() as tmp, Catalog(':memory:') as cat:
            revision = put_mesh(cat.meshes, 'ring', *ring_mesh())
            saved = build_bands(cat, 'band', revision.revision_id, 1, 2)
            band = saved.payload.result
            component, = band.components
            hole, = component.holes
            flipped = replace(component, holes=(BoundaryRing(hole.points[::-1]),))
            moved = replace(component, outer=BoundaryRing(((0, 0), (4, 0), (4, Fraction(9, 4)), (0, Fraction(9, 4)))))
            wrong_cell = replace(component.cells[0], face='absent-face')
            wrong_source = replace(component, cells=(wrong_cell, *component.cells[1:]))
            path = Path(tmp) / 'edited.json'
            for edited, reason in ((flipped, 'orientation'), (moved, 'boundary'), (wrong_source, 'cell')):
                changed = replace(band, components=(edited,))
                self.assertEqual(changed.area, 8)
                product = replace(saved, payload=replace(saved.payload, result=changed))
                path.write_text(json.dumps(build_package(revision, [product]), default=lambda value: value), encoding='utf-8')
                _, report = replay_package(load_package(path))
                self.assertFalse(report.ok)
                self.assertTrue(any(reason in issue.detail for issue in report.issues), report.issues)
            self.assertTrue(audit(read_export(cat, 'band')).ok)
