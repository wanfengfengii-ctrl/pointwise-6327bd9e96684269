import json
import subprocess
import sys
import unittest
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from terrain.archive.api import ProcessingRequestError, build_bands, build_change_band, build_contours
from terrain.archive.catalog import Catalog, CatalogError, ProductBindingConflict, UnknownProduct
from terrain.archive.codec import dumps
from terrain.band.boundaries import BoundaryRing
from terrain.band.change import ChangeComponent, change_result
from terrain.delivery.audit import audit
from terrain.delivery.export import ExportError, build_change_package, build_package, validate_package
from terrain.delivery.read import read_export, read_saved_product
from terrain.delivery.replay import load_package, replay_package
from terrain.mesh.revisions import UnknownRevision, put_mesh
from tests.test_archive import forbid_processing


@contextmanager
def forbid_change_processing():
    code = {change_result.__code__}
    previous = sys.getprofile()

    def observe(frame, event, _arg):
        if event == "call" and frame.f_code in code:
            raise AssertionError("saved change band path recomputed the overlay")

    sys.setprofile(observe)
    try:
        yield
    finally:
        sys.setprofile(previous)


SQUARE = (("a", 0, 0), ("b", 1, 0), ("c", 1, 1), ("d", 0, 1))
AC = (("o0", "a", "b", "c"), ("o1", "a", "c", "d"))
BD = (("n0", "a", "b", "d"), ("n1", "b", "c", "d"))
HIGH = (("o0", "n0", Fraction(1, 16)), ("o1", "n0", Fraction(1, 16)))
LOW = (("o0", "n0", Fraction(3, 16)), ("o0", "n1", Fraction(1, 4)),
       ("o1", "n0", Fraction(3, 16)), ("o1", "n1", Fraction(1, 4)))


def square(heights):
    return [(*point, height) for point, height in zip(SQUARE, heights, strict=True)]


def seed(catalog):
    inputs = (("before", square((0, 0, 0, 0)), AC),
              ("after", square((1, 0, 0, 0)), BD),
              ("ridge", square((0, -1, 0, -1)), AC),
              ("touch", [("b", 1, 0, 0), ("e", 2, -1, 0), ("f", 2, 0, 0)],
               [("east", "b", "e", "f")]),
              ("empty", [], []))
    return tuple(put_mesh(catalog.meshes, request, vertices, faces).revision_id
                 for request, vertices, faces in inputs)


def corners(ring):
    return set(ring.points)


class TestChangeBands(unittest.TestCase):
    def test_hand_examples(self):
        with Catalog(":memory:") as cat:
            old, new, ridge, touch, empty = seed(cat)
            high = build_change_band(cat, "lift", old, new, "1/2", 2)
            self.assertEqual(high.payload.kind, "change_band")
            self.assertEqual(high.payload.revision_ids, (old, new))
            self.assertEqual(high.payload.levels, (Fraction(1, 2), 2))
            part, = high.payload.result.components
            self.assertEqual(high.payload.result.area, Fraction(1, 8))
            self.assertEqual(part.area, Fraction(1, 8))
            self.assertEqual(corners(part.outer), {(0, 0), (Fraction(1, 2), 0), (0, Fraction(1, 2))})
            self.assertEqual(part.holes, ())
            self.assertEqual(part.contributions, HIGH)
            low, = build_change_band(cat, "low", old, new, 0, "1/2").payload.result.components
            self.assertEqual(low.area, Fraction(7, 8))
            self.assertEqual(low.contributions, LOW)
            split = build_change_band(cat, "ridge-band", old, ridge, -2, 0).payload.result
            self.assertEqual(sorted(c.area for c in split.components), [Fraction(1, 2)] * 2)
            for request, revision in (("point-only", touch), ("no-domain", empty)):
                result = build_change_band(cat, request, old, revision, 0, 1).payload.result
                self.assertEqual((result.components, result.area), ((), 0))
            same = build_change_band(cat, "unchanged", old, old, 0, 1).payload.result
            self.assertEqual((same.area, len(same.components)), (1, 1))

    def test_seams_holes_and_point_contacts(self):
        seam_old = [("l", -1, 0, 0), ("a", 0, 0, 0), ("b", 0, 1, 0), ("r", 1, 0, 0)]
        seam_faces = [("lo", "l", "a", "b"), ("ro", "r", "b", "a")]
        seam_new = [("l", -1, 0, 0), ("a", 0, 0, 1), ("b", 0, 1, 1), ("r", 1, 0, 0)]
        valley_new = [("l", -1, 0, 0), ("a", 0, 0, -1), ("b", 0, 1, -1), ("r", 1, 0, 0)]
        # Outer square [-2,2]^2 flat, inner plateau of radius one at height 1;
        # the height-1 ring is a closed upper-bound seam.
        moat_new = [("O", 0, 0, 0), ("R0", -1, -1, 1), ("R1", 1, -1, 1), ("R2", 1, 1, 1),
                    ("R3", -1, 1, 1), ("O0", -2, -2, 0), ("O1", 2, -2, 0), ("O2", 2, 2, 0),
                    ("O3", -2, 2, 0)]
        moat_faces = [("c0", "O", "R0", "R1"), ("c1", "O", "R1", "R2"), ("c2", "O", "R2", "R3"),
                      ("c3", "O", "R3", "R0"), ("r0a", "R0", "R1", "O1"), ("r0b", "R0", "O1", "O0"),
                      ("r1a", "R1", "R2", "O2"), ("r1b", "R1", "O2", "O1"),
                      ("r2a", "R2", "R3", "O3"), ("r2b", "R2", "O3", "O2"),
                      ("r3a", "R3", "R0", "O0"), ("r3b", "R3", "O0", "O3")]
        flat_big = [("O0", -2, -2, 0), ("O1", 2, -2, 0), ("O2", 2, 2, 0), ("O3", -2, 2, 0)]
        with Catalog(":memory:") as cat:
            old = put_mesh(cat.meshes, "seam-old", seam_old, seam_faces)
            for request, vertices, lo, hi, count in (
                    ("upper", seam_new, 0, 1, 2), ("lower", valley_new, -1, 0, 1)):
                new = put_mesh(cat.meshes, f"{request}-new", vertices, seam_faces)
                result = build_change_band(cat, request, old.revision_id, new.revision_id, lo, hi).payload.result
                self.assertEqual((result.area, len(result.components)), (1, count))
            flat = put_mesh(cat.meshes, "moat-old", flat_big, [("q0", "O0", "O1", "O2"), ("q1", "O0", "O2", "O3")])
            moat = put_mesh(cat.meshes, "moat-new", moat_new, moat_faces)
            split = build_change_band(cat, "moat", flat.revision_id, moat.revision_id, 0, 1).payload.result
            self.assertEqual(split.area, 16)
            self.assertEqual(sorted(c.area for c in split.components), [4, 12])
            self.assertEqual(sorted(len(c.holes) for c in split.components), [0, 1])
            merged = build_change_band(cat, "moat-open", flat.revision_id, moat.revision_id, 0, 2).payload.result
            self.assertEqual((merged.area, len(merged.components), merged.components[0].holes),
                             (16, 1, ()))
            for request in ("upper", "lower", "moat", "moat-open"):
                self.assertTrue(audit(read_export(cat, request)).ok)

    def test_point_contact_does_not_join_components(self):
        # Difference is +1 in the east and west quadrants and -1 north/south;
        # the in-band quadrants meet only at the saddle point.
        vertices = [("o", 0, 0, 0), ("e", 1, 0, 1), ("n", 0, 1, -1),
                    ("w", -1, 0, 1), ("s", 0, -1, -1)]
        faces = [("ne", "o", "e", "n"), ("nw", "o", "n", "w"),
                 ("sw", "o", "w", "s"), ("se", "o", "s", "e")]
        with Catalog(":memory:") as cat:
            revision = put_mesh(cat.meshes, "saddle", vertices, faces)
            flat = put_mesh(cat.meshes, "flat",
                            [("o", 0, 0, 0), ("e", 1, 0, 0), ("n", 0, 1, 0),
                             ("w", -1, 0, 0), ("s", 0, -1, 0)], faces)
            band = build_change_band(cat, "up", flat.revision_id, revision.revision_id, 0, 2).payload.result
            self.assertEqual((band.area, len(band.components)), (1, 2))
            self.assertEqual(sorted(c.area for c in band.components), [Fraction(1, 2)] * 2)
            first, second = (set(c.outer.points) for c in band.components)
            self.assertEqual(first & second, {(0, 0)})
            down = build_change_band(cat, "down", flat.revision_id, revision.revision_id, -2, 0).payload.result
            self.assertEqual((down.area, len(down.components)), (1, 2))
            for request in ("up", "down"):
                self.assertTrue(audit(read_export(cat, request)).ok)

    def test_contributions_are_exact_and_sorted(self):
        with Catalog(":memory:") as cat:
            old, new, *_ = seed(cat)
            for request, lo, hi, expected in (
                    ("lift", "1/2", 2, HIGH),
                    ("low", 0, "1/2", LOW)):
                component, = build_change_band(cat, request, old, new, lo, hi).payload.result.components
                self.assertEqual(component.contributions, expected)
                self.assertEqual(tuple(sorted(component.contributions)), expected)
                self.assertEqual(sum((area for _, _, area in component.contributions), Fraction()),
                                 component.area)

    def test_retriangulation_does_not_change_area(self):
        # The same field sampled at the old nodes and at the new nodes: the fit
        # mesh misses the tent peak, so the change band must reflect the exact
        # new field rather than the interpolated fit.
        with Catalog(":memory:") as cat:
            old, new, *_ = seed(cat)
            fit = put_mesh(cat.meshes, "fit", square((0, 0, 0, 0)), BD).revision_id
            fit_band = build_change_band(cat, "fit-band", old, fit, "1/2", 2).payload.result
            self.assertEqual(fit_band.components, ())
            real = build_change_band(cat, "real", old, new, "1/2", 2).payload.result
            self.assertEqual(real.area, Fraction(1, 8))

    def test_binding_and_replay(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "terrain.sqlite"
            with Catalog(path) as cat:
                old, new, *_ = seed(cat)
                saved = build_change_band(cat, "lift", old, new, "1/2", 2)
            with Catalog(path) as cat, forbid_processing():
                self.assertEqual(read_saved_product(cat, "lift"), saved)
                self.assertEqual(build_change_band(cat, "lift", old, new, "2/4", 2), saved)
                for old_id, new_id, lo, hi in ((new, old, "1/2", 2), (old, new, 0, 2), (old, new, "1/2", 3)):
                    with self.assertRaises(ProductBindingConflict):
                        build_change_band(cat, "lift", old_id, new_id, lo, hi)
                with self.assertRaises(ProductBindingConflict):
                    build_bands(cat, "lift", old, "1/2", 2)
                with self.assertRaises(ProductBindingConflict):
                    build_contours(cat, "lift", old, ["1/2"])
                self.assertEqual(cat.load_product("lift"), saved)
            with Catalog(path) as cat:
                reopened = build_change_band(cat, "lift", old, new, "1/2", 2)
                self.assertEqual(reopened, saved)

    def test_invalid_and_missing_inputs(self):
        with Catalog(":memory:") as cat:
            old, new, *_ = seed(cat)
            for lo, hi in ((2, 1), (1, 1), ("1/0", 2), (True, 2), (0, False)):
                with self.subTest(lo=lo, hi=hi), self.assertRaises(ProcessingRequestError):
                    build_change_band(cat, "bad", old, new, lo, hi)
            self.assertIsNone(cat.find_request("bad"))
            with self.assertRaises(UnknownRevision):
                build_change_band(cat, "bad", old, "missing", 0, 1)
            with self.assertRaises(UnknownRevision):
                build_change_band(cat, "bad", "missing", new, 0, 1)
            self.assertIsNone(cat.find_request("bad"))

    def test_publish_failure_leaves_nothing(self):
        for phase in ("product_records", "product_geometry", "product_sources", "product_revisions"):
            with self.subTest(phase=phase), TemporaryDirectory() as directory:
                path = Path(directory) / "archive.sqlite"
                with Catalog(path) as cat:
                    old, new, *_ = seed(cat)
                    keep = build_change_band(cat, "keep", old, new, "1/2", 2)
                    cat.connection.execute(f'''CREATE TRIGGER no_publish BEFORE INSERT ON {phase}
                        WHEN NEW.product_id = 'new' BEGIN SELECT RAISE(ABORT, 'storage phase rejected'); END''')
                    with self.assertRaises(CatalogError):
                        build_change_band(cat, "new", old, new, 0, 1)
                with Catalog(path) as cat:
                    self.assertIsNone(cat.find_request("new"))
                    with self.assertRaises(UnknownProduct):
                        cat.load_product("new")
                    self.assertEqual(cat.load_product("keep"), keep)
                    for table in ("product_records", "product_geometry", "product_sources",
                                  "product_revisions"):
                        rows = cat.connection.execute(f"SELECT DISTINCT product_id FROM {table}").fetchall()
                        self.assertEqual(rows, [("keep",)])
                    cat.connection.execute("DROP TRIGGER no_publish")
                    self.assertEqual(build_change_band(cat, "new", old, new, 0, 1).payload.result.area, 1)

    def test_mixed_delivery_is_rejected(self):
        with Catalog(":memory:") as cat:
            old, new, *_ = seed(cat)
            band = build_bands(cat, "ordinary", old, 0, 1)
            change = build_change_band(cat, "change", old, new, "1/2", 2)
            with self.assertRaises(ValueError):
                read_export(cat, [band.product_id, change.product_id])
            view = read_export(cat, change.product_id)
            self.assertEqual(tuple(revision.revision_id for revision in view.revisions), (old, new))
            self.assertEqual(tuple(product.product_id for product in view.products), ("change",))


class TestChangeDelivery(unittest.TestCase):
    def test_saved_path_does_not_recompute(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "terrain.sqlite"
            package = Path(directory) / "change.json"
            with Catalog(path) as cat:
                old, new, *_ = seed(cat)
                saved = build_change_band(cat, "lift", old, new, "1/2", 2)
            with Catalog(path) as cat, forbid_change_processing():
                self.assertEqual(build_change_band(cat, "lift", old, new, "1/2", 2), saved)
                self.assertEqual(read_saved_product(cat, "lift"), saved)
                view = read_export(cat, "lift", package)
                self.assertEqual(tuple(p.product_id for p in view.products), ("lift",))
            with Catalog(path) as cat:
                # The same request with a different interval must not silently reuse.
                with self.assertRaises(ProductBindingConflict):
                    build_change_band(cat, "lift", old, new, 0, "1/2")

    def test_component_split_and_ring_edits_are_rejected(self):
        with TemporaryDirectory() as directory, Catalog(":memory:") as cat:
            package = Path(directory) / "band.json"
            # Constant difference 1 over the unit square: the whole square is
            # one component with four pair contributions of 1/4 each.
            old = put_mesh(cat.meshes, "old", square((0, 0, 0, 0)), AC)
            new = put_mesh(cat.meshes, "new", square((1, 1, 1, 1)), BD)
            saved = build_change_band(cat, "flat", old.revision_id, new.revision_id, 0, 2)
            component, = saved.payload.result.components
            self.assertEqual(component.contributions, (("o0", "n0", Fraction(1, 4)),
                                                       ("o0", "n1", Fraction(1, 4)),
                                                       ("o1", "n0", Fraction(1, 4)),
                                                       ("o1", "n1", Fraction(1, 4))))
            left = ChangeComponent(BoundaryRing(((0, 0), (1, 0), (1, 1))), (), Fraction(1, 2),
                                   (("o0", "n0", Fraction(1, 4)), ("o1", "n0", Fraction(1, 4))))
            right = ChangeComponent(BoundaryRing(((0, 0), (1, 1), (0, 1))), (), Fraction(1, 2),
                                    (("o0", "n1", Fraction(1, 4)), ("o1", "n1", Fraction(1, 4))))
            split = replace(saved.payload.result, components=(left, right))
            shifted = replace(component, outer=BoundaryRing(tuple((x + Fraction(1, 8), y)
                                                                  for x, y in component.outer.points)))
            self.assertEqual(shifted.area, component.area)
            for edited, reason in ((split, "components"),
                                   (replace(saved.payload.result, components=(shifted,)), "boundar"),
                                   (replace(saved.payload.result,
                                            components=(replace(component, area=Fraction(1, 2)),)),
                                    "area")):
                product = replace(saved, payload=replace(saved.payload, result=edited))
                document = build_change_package((cat.meshes.get_revision(old.revision_id),
                                                 cat.meshes.get_revision(new.revision_id)), [product])
                package.write_text(dumps(document), encoding="ascii")
                _, report = replay_package(load_package(package))
                self.assertFalse(report.ok)
                self.assertTrue(any(reason in issue.detail for issue in report.issues), report.issues)

    def test_package_round_trip_and_offline_audit(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "terrain.sqlite"
            package = Path(directory) / "change.json"
            with Catalog(path) as cat:
                old, new, *_ = seed(cat)
                saved = build_change_band(cat, "lift", old, new, "1/2", 2)
                view = read_export(cat, "lift", package)
            self.assertEqual(tuple(r.revision_id for r in view.revisions), (old, new))
            document = json.loads(package.read_bytes())
            self.assertEqual(document["version"], 2)
            self.assertEqual([snapshot["revision"] for snapshot in document["meshes"]], [old, new])
            self.assertEqual(document["manifest"]["revisions"], [old, new])
            self.assertEqual(document["products"][0]["binding"],
                             {"kind": "change_band", "revisions": [old, new], "levels": ["1/2", "2/1"]})
            first = package.read_bytes()
            path.unlink()
            with forbid_processing():
                loaded = load_package(package)
                offline_view, report = replay_package(loaded)
            self.assertEqual(offline_view, view)
            self.assertTrue(report.ok, report.issues)
            self.assertEqual(dict(report.band_areas), {"lift": Fraction(1, 8)})
            self.assertEqual(offline_view.products[0].payload.result.components[0].contributions, HIGH)
            process = subprocess.run([sys.executable, "-B", "-m", "terrain.delivery.replay", str(package)],
                                     check=False, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 0, process.stderr)
            output = json.loads(process.stdout)
            self.assertEqual((output["revisions"], output["products"], output["band_areas"]),
                             ([old, new], ["lift"], {"lift": "1/8"}))
            self.assertNotIn("revision", output)
            self.assertFalse(path.exists())
            self.assertTrue(saved)

    def test_wrong_source_and_geometry_rejected(self):
        with TemporaryDirectory() as directory:
            package = Path(directory) / "change.json"
            with Catalog(":memory:") as cat:
                old, new, *_ = seed(cat)
                build_change_band(cat, "lift", old, new, "1/2", 2)
                read_export(cat, "lift", package)
            original = package.read_bytes()
            document = json.loads(original)
            row, = document["products"]

            def resign(edited):
                edited["manifest"]["entries"][0][-1] = sha256(dumps(edited["products"][0]).encode("ascii")).hexdigest()
                target = Path(directory) / "edited.json"
                target.write_text(dumps(edited), encoding="ascii")
                return target

            wrong_new = deepcopy(document)
            wrong_new["products"][0]["sources"][0][1] = ["o0", "n1", "1/16"]
            _, report = replay_package(load_package(resign(wrong_new)))
            self.assertFalse(report.ok)
            self.assertTrue(any("contributions" in issue.detail for issue in report.issues), report.issues)

            wrong_area = deepcopy(document)
            wrong_area["products"][0]["sources"][0] = [["o0", "n0", "1/8"], ["o1", "n0", "0/1"]]
            wrong_area["products"][0]["geometry"][0][2] = "1/8"
            _, report = replay_package(load_package(resign(wrong_area)))
            self.assertFalse(report.ok)

            wrong_mesh = deepcopy(document)
            wrong_mesh["meshes"][1]["mesh"]["vertices"][0][3] = "0/1"
            target = resign(wrong_mesh)
            with self.assertRaises(ExportError):
                load_package(target)

            swapped = deepcopy(document)
            swapped["meshes"] = [document["meshes"][1], document["meshes"][0]]
            target = resign(swapped)
            with self.assertRaises(ExportError):
                load_package(target)
            self.assertEqual(package.read_bytes(), original)

    def test_legacy_package_still_validates(self):
        with Catalog(":memory:") as cat:
            old, *_ = seed(cat)
            band = build_bands(cat, "band", old, 0, 2)
            contours = build_contours(cat, "line", old, 1)
            document = build_package(cat.meshes.get_revision(old), (band, contours))
            manifest, revisions, products = validate_package(document)
            self.assertEqual((revisions[0].revision_id, len(products)), (old, 2))
            self.assertEqual(manifest.revisions, (old,))
            with Catalog(":memory:") as other:
                new = seed(other)[1]
                self.assertNotEqual(new, old)
            edited = deepcopy(document)
            edited["version"] = 2
            with self.assertRaises(ExportError):
                validate_package(edited)


if __name__ == "__main__":
    unittest.main()
