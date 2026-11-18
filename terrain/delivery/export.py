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
    revision_id: str | None
    entries: tuple[tuple[str, str, tuple[str, ...], str], ...]
    revisions: tuple[str, ...] = ()


def _manifest_entry(product):
    binding = product['binding']
    digest = hashlib.sha256(dumps(product).encode('ascii')).hexdigest()
    return [product['id'], binding['kind'], binding['levels'], digest]


def build_package(revision, products):
    products = tuple(sorted(products, key=lambda p: p.product_id))
    if any(product.payload.kind == 'change_band' for product in products):
        raise ExportError('change band products require the version 2 two-snapshot package')
    require_same_revision(revision.revision_id, *products)
    documents = []
    for product in products:
        binding, geometry, sources = encode_product(product.payload)
        documents.append({'id': product.product_id, 'binding': binding, 'geometry': geometry, 'sources': sources})
    return {'version': 1, 'mesh': encode_mesh_snapshot(revision), 'products': documents,
            'manifest': {'revision': revision.revision_id, 'entries': [_manifest_entry(p) for p in documents]}}


def build_change_package(revisions, products):
    """Build the version 2 package holding both survey snapshots."""
    products = tuple(sorted(products, key=lambda p: p.product_id))
    if len(revisions) != 2 or len(products) != 1:
        raise ExportError('change band package requires two snapshots and one product')
    product = products[0]
    if product.payload.kind != 'change_band' or product.payload.revision_ids != tuple(
            revision.revision_id for revision in revisions):
        raise ExportError('change band package binding differs from its snapshots')
    binding, geometry, sources = encode_product(product.payload)
    document = {'id': product.product_id, 'binding': binding, 'geometry': geometry, 'sources': sources}
    return {'version': 2,
            'meshes': [encode_mesh_snapshot(revision) for revision in revisions],
            'products': [document],
            'manifest': {'revisions': [revision.revision_id for revision in revisions],
                         'entries': [_manifest_entry(document)]}}


def _validate_legacy(document):
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
        if payload.kind == 'change_band':
            raise CodecError('change band requires two mesh snapshots')
        products.append(ArchivedProduct(identity(row['id']), payload))
        entries.append(_manifest_entry(row))
    ids = [product.product_id for product in products]
    if ids != sorted(set(ids)):
        raise CodecError('duplicate or unordered packaged products')
    require_same_revision(revision.revision_id, *products)
    if document['manifest'] != {'revision': revision.revision_id, 'entries': entries}:
        raise CodecError('delivery manifest differs from saved product binding or content')
    manifest = PackageManifest(revision.revision_id, tuple((p, k, tuple(v), d) for p, k, v, d in entries),
                               (revision.revision_id,))
    return manifest, (revision,), tuple(products)


def _validate_change(document):
    if set(document) != {'version', 'meshes', 'products', 'manifest'}:
        raise CodecError('invalid delivery version or structure')
    meshes = document['meshes']
    if not isinstance(meshes, list) or len(meshes) != 2:
        raise CodecError('change band delivery requires two mesh snapshots')
    revisions = tuple(decode_mesh_snapshot(snapshot) for snapshot in meshes)
    rows = document['products']
    if not isinstance(rows, list) or len(rows) != 1:
        raise CodecError('change band delivery holds exactly one product')
    row = rows[0]
    if not isinstance(row, dict) or set(row) != {'id', 'binding', 'geometry', 'sources'}:
        raise CodecError('invalid packaged product structure')
    payload = decode_product(row['binding'], row['geometry'], row['sources'])
    if payload.kind != 'change_band':
        raise CodecError('change band delivery requires a change band product')
    if payload.revision_ids != tuple(revision.revision_id for revision in revisions):
        raise CodecError('change band binding differs from the packaged mesh snapshots')
    product = ArchivedProduct(identity(row['id']), payload)
    entries = [_manifest_entry(row)]
    if document['manifest'] != {'revisions': [revision.revision_id for revision in revisions],
                                'entries': entries}:
        raise CodecError('delivery manifest differs from saved product binding or content')
    manifest = PackageManifest(None, tuple((p, k, tuple(v), d) for p, k, v, d in entries),
                               tuple(revision.revision_id for revision in revisions))
    return manifest, revisions, (product,)


def validate_package(document):
    try:
        if not isinstance(document, dict) or type(document.get('version')) is not int:
            raise CodecError('invalid delivery version or structure')
        if document['version'] == 1:
            return _validate_legacy(document)
        if document['version'] == 2:
            return _validate_change(document)
        raise CodecError('unsupported delivery version')
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
