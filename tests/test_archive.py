import sqlite3
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from terrain.archive.api import build_bands, build_contours
from terrain.archive.catalog import Catalog, CatalogError, ProductBindingConflict, UnknownProduct
from terrain.band.clipping import clip_band_cells
from terrain.contour.incidence import build_level_incidence
from terrain.mesh.revisions import put_mesh
from tests.test_contours import flat_square


@contextmanager
def forbid_processing():
    code = {build_level_incidence.__code__, clip_band_cells.__code__}
    previous = sys.getprofile()
    def observe(frame, event, _arg):
        if event == 'call' and frame.f_code in code:
            raise AssertionError('saved-product path entered terrain processing')
    sys.setprofile(observe)
    try:
        yield
    finally:
        sys.setprofile(previous)


class TestArchive(unittest.TestCase):
    def test_publish_failure(self):
        for phase in ('product_records', 'product_geometry', 'product_sources'):
            with self.subTest(phase=phase), TemporaryDirectory() as directory:
                path = Path(directory) / 'archive.sqlite'
                with Catalog(path) as cat:
                    mesh = put_mesh(cat.meshes, 'mesh', *flat_square())
                    old = build_contours(cat, 'old', mesh.revision_id, 1)
                    cat.connection.execute(f'''CREATE TRIGGER no_publish BEFORE INSERT ON {phase}
                        WHEN NEW.product_id = 'new' BEGIN SELECT RAISE(ABORT, 'storage phase rejected'); END''')
                    with self.assertRaises(CatalogError) as error:
                        build_bands(cat, 'new', mesh.revision_id, 1, 2)
                    self.assertIsInstance(error.exception.__cause__, sqlite3.IntegrityError)
                with Catalog(path) as cat:
                    self.assertEqual(cat.load_product('old'), old)
                    with self.assertRaises(UnknownProduct):
                        cat.load_product('new')
                    self.assertIsNone(cat.find_request('new'))
                    for table in ('product_records', 'product_geometry', 'product_sources'):
                        self.assertEqual(cat.connection.execute(f'SELECT product_id FROM {table}').fetchall(), [('old',)])
                    cat.connection.execute('DROP TRIGGER no_publish')
                    new = build_bands(cat, 'new', mesh.revision_id, 1, 2)
                    self.assertEqual(new.payload.result.area, 1)
                    self.assertEqual(cat.load_product('new'), new)

    def test_request_binding(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'archive.sqlite'
            with Catalog(path) as cat:
                mesh = put_mesh(cat.meshes, 'one', *flat_square())
                other = put_mesh(cat.meshes, 'two', *flat_square(slope=True))
                old = build_contours(cat, 'request', mesh.revision_id, [2, 1, '2/2'])
                band = build_bands(cat, 'band', mesh.revision_id, 1, 2)
            with Catalog(path) as cat, forbid_processing():
                self.assertEqual(build_contours(cat, 'request', mesh.revision_id, [1, 2]), old)
                self.assertEqual(build_bands(cat, 'band', mesh.revision_id, '2/2', 2), band)
                for revision, levels in ((other.revision_id, [1, 2]), (mesh.revision_id, [1, 3])):
                    with self.assertRaises(ProductBindingConflict):
                        build_contours(cat, 'request', revision, levels)
                with self.assertRaises(ProductBindingConflict):
                    build_bands(cat, 'request', mesh.revision_id, 1, 2)
                self.assertEqual(cat.load_product('request'), old)
