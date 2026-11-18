import argparse
import json
import sys
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from terrain.archive.catalog import ArchivedProduct, Catalog
from terrain.archive.jobs import (bind_change_request, bind_processing_request, run_band_job, run_change_job,
                                  run_contour_job)
from terrain.delivery.audit import audit
from terrain.delivery.read import read_export, read_saved_product
from terrain.delivery.replay import load_package, replay_package
from terrain.field.query import sample
from terrain.mesh.input import MeshInputError, parse_rational
from terrain.mesh.revisions import put_mesh


class ProcessingRequestError(ValueError):
    pass


def build_contours(catalog, request, revision_id, levels):
    try:
        if isinstance(levels, (int, Fraction, str)):
            levels = (levels,)
        if isinstance(levels, (bytes, dict)):
            raise MeshInputError('expected a threshold or iterable of thresholds')
        values = tuple(sorted({parse_rational(value) for value in levels}))
    except (TypeError, MeshInputError) as exc:
        raise ProcessingRequestError('invalid contour thresholds') from exc
    job = bind_processing_request(catalog, request, 'contours', revision_id, values)
    return job if isinstance(job, ArchivedProduct) else run_contour_job(catalog, job)


def build_bands(catalog, request, revision_id, lo, hi):
    try:
        values = parse_rational(lo), parse_rational(hi)
    except MeshInputError as exc:
        raise ProcessingRequestError('invalid band thresholds') from exc
    if values[0] >= values[1]:
        raise ProcessingRequestError('band requires lo < hi')
    job = bind_processing_request(catalog, request, 'band', revision_id, values)
    return job if isinstance(job, ArchivedProduct) else run_band_job(catalog, job)


def build_change_band(catalog, request, old_revision_id, new_revision_id, lo, hi):
    try:
        values = parse_rational(lo), parse_rational(hi)
    except MeshInputError as exc:
        raise ProcessingRequestError('invalid change band thresholds') from exc
    if values[0] >= values[1]:
        raise ProcessingRequestError('change band requires lo < hi')
    job = bind_change_request(catalog, request, old_revision_id, new_revision_id, values)
    if isinstance(job, ArchivedProduct):
        return job
    job, new_revision = job
    return run_change_job(catalog, job, new_revision)


def _day_shift(data, directory):
    destination = directory / 'delivery.json'
    with Catalog(directory / 'terrain.sqlite') as catalog:
        revision = put_mesh(catalog.meshes, data['request'], data['vertices'], data['faces'])
        point = sample(catalog.meshes, revision.revision_id, *data['query'])
        line = build_contours(catalog, 'lines', revision.revision_id, data['threshold'])
        bands = [build_bands(catalog, f'band-{i}', revision.revision_id, *interval)
                 for i, interval in enumerate(data['bands'])]
        ids = [line.product_id, *(band.product_id for band in bands)]
        view = read_export(catalog, ids)
        report = audit(view)
        if not report.ok:
            raise ValueError('PLATEAU_TOPOLOGY_MISMATCH ' + repr(report.issues))
        read_export(catalog, ids, destination)
    offline_view, offline = replay_package(load_package(destination))
    geometries = [band.payload.result for band in bands]
    actual = {'height': str(point.height), 'areas': [str(band.area) for band in geometries],
              'components': [len(band.components) for band in geometries],
              'arcs': len(line.payload.result[0].arcs)}
    expected = {'height': '3/2', 'areas': ['0', '3/2'], 'components': [0, 1], 'arcs': 4}
    downstream = {'audit_ok': offline.ok, 'same_revision': offline_view.revision.revision_id == revision.revision_id,
                  'areas': [str(area) for _, area in offline.band_areas]}
    downstream_expected = {'audit_ok': True, 'same_revision': True, 'areas': expected['areas']}
    if actual != expected or downstream != downstream_expected:
        raise ValueError(f'PLATEAU_TOPOLOGY_MISMATCH expected={expected!r} actual={actual!r} offline={downstream!r}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'V3.platform_ownership', 'input': data, 'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream,
        'code_paths': ['terrain/field/query.py', 'terrain/contour/assembly.py', 'terrain/band/clipping.py',
                       'terrain/band/sectors.py', 'terrain/delivery/replay.py'],
        'oracle': '单位平台面积为 1，东侧斜坡三角形面积为 1/2。斜坡高程等于 x，平台外边共四条。'
    }, ensure_ascii=False))


def _change_demo(directory):
    from terrain.archive.catalog import ProductBindingConflict

    square = (('a', 0, 0), ('b', 1, 0), ('c', 1, 1), ('d', 0, 1))
    before = [(*point, 0) for point in square]
    after = [(*point, height) for point, height in zip(square, (1, 0, 0, 0), strict=True)]
    old_faces = [('o0', 'a', 'b', 'c'), ('o1', 'a', 'c', 'd')]
    new_faces = [('n0', 'a', 'b', 'd'), ('n1', 'b', 'c', 'd')]
    database = directory / 'terrain.sqlite'
    package = directory / 'change.json'
    with Catalog(database) as catalog:
        old = put_mesh(catalog.meshes, 'survey-before', before, old_faces)
        new = put_mesh(catalog.meshes, 'survey-after', after, new_faces)
        product = build_change_band(catalog, 'lift', old.revision_id, new.revision_id, '1/2', 2)
        view = read_export(catalog, 'lift', package)
        assert tuple(revision.revision_id for revision in view.revisions) == (old.revision_id, new.revision_id)
        try:
            build_change_band(catalog, 'lift', new.revision_id, old.revision_id, '1/2', 2)
        except ProductBindingConflict:
            rejected = True
        else:
            raise ValueError('CHANGE_BAND_BINDING_MISMATCH reversed survey reused the request')
    with Catalog(database) as catalog:
        saved = read_saved_product(catalog, 'lift')
        assert saved == product
    database.unlink()
    offline_view, report = replay_package(load_package(package))
    if not report.ok:
        raise ValueError('CHANGE_BAND_OFFLINE_MISMATCH ' + repr(report.issues))
    part, = offline_view.products[0].payload.result.components
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'V4.change_band_delivery',
        'input': {'old_vertices': before, 'old_faces': old_faces,
                  'new_vertices': after, 'new_faces': new_faces, 'levels': ['1/2', '2']},
        'expected': {'area': '1/8', 'contributions': [['o0', 'n0', '1/16'], ['o1', 'n0', '1/16']]},
        'actual': {'revisions': [old.revision_id, new.revision_id],
                   'area': str(dict(report.band_areas)['lift']),
                   'contributions': [[o, n, str(value)] for o, n, value in part.contributions]},
        'downstream_expected': {'offline_ok': True, 'reversed_rejected': True},
        'downstream_actual': {'offline_ok': report.ok, 'reversed_rejected': rejected},
        'code_paths': ['terrain/band/change.py', 'terrain/archive/api.py', 'terrain/archive/catalog.py',
                       'terrain/delivery/export.py', 'terrain/delivery/replay.py'],
        'oracle': '在 [1/2,2) 内新面 ABD 的差值 1-x-y 被旧对角线切成两片，面积各 1/16。'
    }, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run the terrain day-shift sample')
    parser.add_argument('--sample', type=Path, default=Path('samples/day_shift.json'))
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--change-demo', action='store_true',
                        help='run the two-survey change band and offline delivery demo')
    args = parser.parse_args(argv)
    try:
        if args.change_demo:
            if args.directory is None:
                with TemporaryDirectory(prefix='terrain-change-') as directory:
                    _change_demo(Path(directory))
            else:
                args.directory.mkdir(parents=True, exist_ok=True)
                _change_demo(args.directory)
            return 0
        data = json.loads(args.sample.read_text(encoding='utf-8'))
        if args.directory is None:
            with TemporaryDirectory(prefix='terrain-demo-') as directory:
                _day_shift(data, Path(directory))
        else:
            args.directory.mkdir(parents=True, exist_ok=True)
            _day_shift(data, args.directory)
    except (OSError, ValueError, RuntimeError, LookupError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
