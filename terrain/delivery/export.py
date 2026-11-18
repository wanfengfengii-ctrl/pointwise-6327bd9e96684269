import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from terrain.archive.catalog import ArchivedProduct
from terrain.archive.codec import (CodecError, decode_mesh_snapshot, decode_product, dumps,
                                   encode_mesh_snapshot, encode_product)
from terrain.field.binding import require_same_revision
from terrain.mesh.input import identity


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PackageManifest:
    revision_id: str
    entries: tuple[tuple[str, str, tuple[str, ...], str], ...]


def _manifest_entry(product):
    binding = product['binding']
    digest = hashlib.sha256(dumps(product).encode('ascii')).hexdigest()
    return [product['id'], binding['kind'], binding['levels'], digest]


def build_package(revision, products):
    products = tuple(sorted(products, key=lambda p: p.product_id))
    require_same_revision(revision.revision_id, *products)
    documents = []
    for product in products:
        binding, geometry, sources = encode_product(product.payload)
        documents.append({'id': product.product_id, 'binding': binding, 'geometry': geometry, 'sources': sources})
    return {'version': 1, 'mesh': encode_mesh_snapshot(revision), 'products': documents,
            'manifest': {'revision': revision.revision_id, 'entries': [_manifest_entry(p) for p in documents]}}


def validate_package(document):
    try:
        if (not isinstance(document, dict) or set(document) != {'version', 'mesh', 'products', 'manifest'} or
                type(document['version']) is not int or document['version'] != 1):
            raise CodecError('invalid delivery version or structure')
        revision = decode_mesh_snapshot(document['mesh'])
        products = []
        entries = []
        if not isinstance(document['products'], list) or not document['products']:
            raise CodecError('delivery requires saved products')
        for row in document['products']:
            if not isinstance(row, dict) or set(row) != {'id', 'binding', 'geometry', 'sources'}:
                raise CodecError('invalid packaged product structure')
            payload = decode_product(row['binding'], row['geometry'], row['sources'])
            products.append(ArchivedProduct(identity(row['id']), payload))
            entries.append(_manifest_entry(row))
        ids = [product.product_id for product in products]
        if ids != sorted(set(ids)):
            raise CodecError('duplicate or unordered packaged products')
        require_same_revision(revision.revision_id, *products)
        if document['manifest'] != {'revision': revision.revision_id, 'entries': entries}:
            raise CodecError('delivery manifest differs from saved product binding or content')
        manifest = PackageManifest(revision.revision_id, tuple((p, k, tuple(v), d) for p, k, v, d in entries))
        return manifest, revision, tuple(products)
    except (ValueError, TypeError, KeyError) as exc:
        raise ExportError(f'invalid delivery binding or package: {exc}') from exc


def export_package(document, destination):
    destination = Path(destination)
    temporary = None
    try:
        with NamedTemporaryFile(mode='wb', prefix=f'.{destination.name}.', suffix='.tmp',
                                dir=destination.parent, delete=False) as staged:
            temporary = Path(staged.name)
            staged.write((dumps(document) + '\n').encode('ascii'))
            staged.flush()
            os.fsync(staged.fileno())
        validate_package(json.loads(temporary.read_bytes()))
        os.replace(temporary, destination)
        temporary = None
    except (OSError, ValueError) as exc:
        raise ExportError(f'cannot export {destination}: {exc}') from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination
