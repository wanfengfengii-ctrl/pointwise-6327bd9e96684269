import argparse
import json
import sys
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from terrain.archive.catalog import ArchivedProduct, Catalog
from terrain.archive.jobs import bind_processing_request, run_band_job, run_contour_job
from terrain.delivery.audit import audit
from terrain.delivery.read import read_export
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


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run the terrain day-shift sample')
    parser.add_argument('--sample', type=Path, default=Path('samples/day_shift.json'))
    parser.add_argument('--directory', type=Path)
    args = parser.parse_args(argv)
    try:
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
