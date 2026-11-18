import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from terrain.delivery.audit import audit
from terrain.delivery.export import ExportError, PackageManifest, validate_package
from terrain.delivery.read import DeliveryView


@dataclass(frozen=True, slots=True)
class OfflinePackage:
    manifest: PackageManifest
    view: DeliveryView


def load_package(path):
    try:
        document = json.loads(Path(path).read_bytes())
    except (OSError, ValueError) as exc:
        raise ExportError(f'cannot read delivery package: {exc}') from exc
    manifest, revisions, products = validate_package(document)
    return OfflinePackage(manifest, DeliveryView(revisions[0], products, revisions))


def replay_package(package):
    return package.view, audit(package.view)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read and audit a terrain delivery package offline')
    parser.add_argument('package', type=Path)
    args = parser.parse_args(argv)
    try:
        view, report = replay_package(load_package(args.package))
    except ExportError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    result = {'products': [p.product_id for p in view.products], 'ok': report.ok,
              'band_areas': {key: str(area) for key, area in report.band_areas},
              'issues': [{'code': issue.code, 'product': issue.product, 'detail': issue.detail}
                         for issue in report.issues]}
    if view.is_change_band:
        result['revisions'] = [revision.revision_id for revision in view.revisions]
    else:
        result['revision'] = view.revision.revision_id
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if report.ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
