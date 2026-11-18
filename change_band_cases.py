import argparse
import json
from fractions import Fraction as F
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from terrain.archive.api import build_bands
from terrain.archive.catalog import Catalog, ProductBindingConflict
from terrain.archive.codec import dumps
from terrain.delivery.audit import audit
from terrain.delivery.read import read_export, read_saved_product
from terrain.delivery.replay import load_package, replay_package
from terrain.field.query import sample
from terrain.mesh.revisions import put_mesh
from terrain.mesh.validation import orient2d


SQUARE = (("a", 0, 0), ("b", 1, 0), ("c", 1, 1), ("d", 0, 1))
AC = (("o0", "a", "b", "c"), ("o1", "a", "c", "d"))
BD = (("n0", "a", "b", "d"), ("n1", "b", "c", "d"))
HIGH = (("o0", "n0", F(1, 16)), ("o1", "n0", F(1, 16)))
LOW = (("o0", "n0", F(3, 16)), ("o0", "n1", F(1, 4)),
       ("o1", "n0", F(3, 16)), ("o1", "n1", F(1, 4)))


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


def verify_fixture():
    with Catalog(":memory:") as catalog:
        old, new, ridge, _, _ = seed(catalog)
        vertices = [(*point, sample(catalog.meshes, new, *point[1:]).height
                     - sample(catalog.meshes, old, *point[1:]).height) for point in SQUARE]
        fit = put_mesh(catalog.meshes, "old-node-fit", vertices, AC).revision_id
        approximation = build_bands(catalog, "old-fit-band", fit, "1/2", 2)
        center = (F(1, 2), F(1, 2))
        assert sample(catalog.meshes, new, *center).height == 0
        assert sample(catalog.meshes, fit, *center).height == F(1, 2)
        assert approximation.payload.result.area == F(1, 4)
        split = build_bands(catalog, "v1-ridge", ridge, -2, 0).payload.result
        assert sorted(component.area for component in split.components) == [F(1, 2)] * 2
        assert audit(read_export(catalog, "v1-ridge")).ok
    print("V1 old-node fit area 1/4. Excluded ridge has two components of area 1/2 each.")


def corners(ring):
    points = ring.points
    return {p for i, p in enumerate(points)
            if orient2d(points[i - 1], p, points[(i + 1) % len(points)])}


def reject_wrong_source(path):
    document = json.loads(path.read_bytes())
    row, = document["products"]
    original = row["sources"][0][0]
    assert original == ["o0", "n0", "1/16"]
    original[1] = "n1"
    document["manifest"]["entries"][0][-1] = sha256(dumps(row).encode("ascii")).hexdigest()
    edited = path.with_name("wrong-source.json")
    edited.write_text(dumps(document), encoding="ascii")
    _, report = replay_package(load_package(edited))
    assert not report.ok, "offline audit accepted the wrong, existing new face"


def exercise():
    from terrain.archive.api import build_change_band

    with TemporaryDirectory() as tmp:
        directory = Path(tmp)
        path = directory / "terrain.sqlite"
        delivery = directory / "change.json"
        with Catalog(path) as catalog:
            old, new, ridge, touch, empty = seed(catalog)
            high = build_change_band(catalog, "lift", old, new, "1/2", 2).payload.result
            part, = high.components
            assert high.area == part.area == F(1, 8)
            assert corners(part.outer) == {(0, 0), (F(1, 2), 0), (0, F(1, 2))}
            assert part.holes == () and part.contributions == HIGH
            low, = build_change_band(catalog, "low", old, new, 0, "1/2").payload.result.components
            assert low.area == F(7, 8) and low.contributions == LOW
            split = build_change_band(catalog, "ridge-band", old, ridge, -2, 0).payload.result
            assert sorted(c.area for c in split.components) == [F(1, 2)] * 2
            for request, revision in (("point-only", touch), ("no-domain", empty)):
                result = build_change_band(catalog, request, old, revision, 0, 1).payload.result
                assert result.components == () and result.area == 0
            same = build_change_band(catalog, "unchanged", old, old, 0, 1).payload.result
            assert same.area == 1 and len(same.components) == 1
        with Catalog(path) as catalog:
            saved = read_saved_product(catalog, "lift")
            assert saved.payload.revision_ids == (old, new)
            assert saved.payload.result.components[0].contributions == HIGH
            replayed = build_change_band(catalog, "lift", old, new, "2/4", 2)
            assert replayed.payload.result.components[0].contributions == HIGH
            try:
                build_change_band(catalog, "lift", new, old, "1/2", 2)
            except ProductBindingConflict:
                pass
            else:
                raise AssertionError("reversed survey binding reused the request")
            read_export(catalog, "lift", delivery)
        path.unlink()
        view, report = replay_package(load_package(delivery))
        assert tuple(revision.revision_id for revision in view.revisions) == (old, new)
        assert report.ok and dict(report.band_areas)["lift"] == F(1, 8), report.issues
        pairs = view.products[0].payload.result.components[0].contributions
        assert pairs == HIGH
        reject_wrong_source(delivery)
        print(json.dumps({"request": "lift", "revisions": [old, new],
                          "area": str(dict(report.band_areas)["lift"]),
                          "contributions": [[a, b, str(area)] for a, b, area in pairs],
                          "offline_ok": report.ok, "wrong_source_rejected": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check the survey change examples")
    parser.add_argument("--exercise", action="store_true", help="run after implementing change bands")
    args = parser.parse_args()
    (exercise if args.exercise else verify_fixture)()
