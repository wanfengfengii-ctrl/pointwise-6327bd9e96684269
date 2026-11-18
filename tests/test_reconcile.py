import json
import subprocess
import sys
import unittest
from fractions import Fraction

from terrain.band import independent
from terrain.delivery.reconcile import check_scenario, exercise
from terrain.archive.catalog import Catalog
from terrain.archive.api import build_change_band
from terrain.mesh.revisions import put_mesh


class TestIndependentReconciliation(unittest.TestCase):
    def test_all_scenarios_clean(self):
        report = exercise(divisions=6)
        self.assertTrue(report["ok"], [entry for entry in report["scenarios"] if entry["issues"]])
        self.assertGreater(report["points_checked"], 100)
        self.assertEqual([entry["scenario"] for entry in report["scenarios"]],
                         ["lift", "ridge", "hole", "crack", "unchanged", "zero-difference",
                          "empty", "point-contact", "upper-seam"])

    def test_cli_exit_code(self):
        good = subprocess.run([sys.executable, "-B", "-m", "terrain.delivery.reconcile",
                               "--divisions", "5"], check=False, capture_output=True, text=True, timeout=120)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertTrue(json.loads(good.stdout)["ok"])
        bad = subprocess.run([sys.executable, "-B", "-m", "terrain.delivery.reconcile",
                              "--divisions", "0"], check=False, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(bad.returncode, 0)

    def test_independent_algorithm_disagrees_with_wrong_pairs(self):
        with Catalog(":memory:") as cat:
            old = put_mesh(cat.meshes, "before",
                           [(name, x, y, 0) for name, x, y in
                            (("a", 0, 0), ("b", 1, 0), ("c", 1, 1), ("d", 0, 1))],
                           (("o0", "a", "b", "c"), ("o1", "a", "c", "d")))
            new = put_mesh(cat.meshes, "after",
                           [("a", 0, 0, 1), ("b", 1, 0, 0), ("c", 1, 1, 0), ("d", 0, 1, 0)],
                           (("n0", "a", "b", "d"), ("n1", "b", "c", "d")))
            product = build_change_band(cat, "lift", old.revision_id, new.revision_id, "1/2", 2)
            from dataclasses import replace
            from terrain.band.boundaries import BoundaryRing
            component, = product.payload.result.components
            original = component.contributions
            wrong = replace(component, contributions=(("o0", "n1", Fraction(1, 8)),))
            result = replace(product.payload.result, components=(wrong,))
            _, issues = check_scenario(cat.meshes, result, old, new, Fraction(1, 2), 2, 6)
            self.assertTrue(any("per-pair" in issue or "contributions" in issue for issue in issues), issues)
            _, clean = check_scenario(cat.meshes, product.payload.result, old, new, Fraction(1, 2), 2, 6)
            self.assertEqual(clean, [])
            self.assertEqual(component.contributions, original)


if __name__ == "__main__":
    unittest.main()
