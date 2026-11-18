import json
import sqlite3
from dataclasses import dataclass

from terrain.archive.codec import ProductPayload, decode_product, dumps, encode_product
from terrain.mesh.input import identity
from terrain.mesh.revisions import MeshRevisionStore


class CatalogError(RuntimeError):
    pass


class ProductBindingConflict(ValueError):
    pass


class UnknownProduct(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ArchivedProduct:
    product_id: str
    payload: ProductPayload

    @property
    def revision_id(self):
        return self.payload.revision_id


def binding_revisions(payload):
    if payload.revision_ids is not None:
        return tuple(payload.revision_ids)
    return (payload.revision_id,)


def require_product_binding(product, kind, revision_ids, levels):
    payload = product.payload
    if isinstance(revision_ids, str):
        revision_ids = (revision_ids,)
    if (payload.kind, binding_revisions(payload), payload.levels) != (kind, tuple(revision_ids), levels):
        raise ProductBindingConflict(f'request {product.product_id!r} already has a different binding')
    return product


class Catalog:
    def __init__(self, path):
        self.connection = sqlite3.connect(path)
        self.meshes = MeshRevisionStore(self.connection)
        try:
            self.connection.execute('PRAGMA foreign_keys = ON')
            self.initialize()
        except BaseException:
            self.connection.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.connection.close()

    def initialize(self):
        self.meshes.initialize()
        with self.connection:
            self.connection.execute('''CREATE TABLE IF NOT EXISTS product_records (
                product_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                revision_id TEXT NOT NULL REFERENCES mesh_revisions(revision_id), levels TEXT NOT NULL)''')
            self.connection.execute('''CREATE TABLE IF NOT EXISTS product_geometry (
                product_id TEXT PRIMARY KEY REFERENCES product_records(product_id), payload TEXT NOT NULL)''')
            self.connection.execute('''CREATE TABLE IF NOT EXISTS product_sources (
                product_id TEXT PRIMARY KEY REFERENCES product_records(product_id), payload TEXT NOT NULL)''')
            self.connection.execute('''CREATE TABLE IF NOT EXISTS product_revisions (
                product_id TEXT NOT NULL REFERENCES product_records(product_id),
                ordinal INTEGER NOT NULL, revision_id TEXT NOT NULL REFERENCES mesh_revisions(revision_id),
                PRIMARY KEY (product_id, ordinal))''')

    def find_request(self, request):
        identity(request)
        row = self.connection.execute('''SELECT r.kind, r.revision_id, r.levels, g.payload, s.payload
            FROM product_records r LEFT JOIN product_geometry g USING(product_id)
            LEFT JOIN product_sources s USING(product_id) WHERE r.product_id = ?''', (request,)).fetchone()
        if row is None:
            return None
        kind, revision, levels, geometry, sources = row
        try:
            if kind == 'change_band':
                revision_ids = tuple(value for (value,) in self.connection.execute(
                    'SELECT revision_id FROM product_revisions WHERE product_id = ? ORDER BY ordinal',
                    (request,)).fetchall())
                if len(revision_ids) != 2:
                    raise CatalogError(f'saved product {request!r} is incomplete or invalid')
                binding = {'kind': kind, 'revisions': list(revision_ids), 'levels': json.loads(levels)}
            else:
                binding = {'kind': kind, 'revision': revision, 'levels': json.loads(levels)}
            payload = decode_product(binding, json.loads(geometry), json.loads(sources))
        except (TypeError, ValueError) as exc:
            raise CatalogError(f'saved product {request!r} is incomplete or invalid') from exc
        return ArchivedProduct(request, payload)

    def load_product(self, product_id):
        product = self.find_request(product_id)
        if product is None:
            raise UnknownProduct(product_id)
        return product

    def publish_product(self, product):
        payload = product.payload
        product_id = identity(product.product_id)
        binding, geometry, sources = encode_product(payload)
        revisions = binding_revisions(payload)
        try:
            with self.connection:
                self.connection.execute('BEGIN IMMEDIATE')
                previous = self.find_request(product_id)
                if previous is not None:
                    return require_product_binding(previous, payload.kind, revisions, payload.levels)
                self.connection.execute('INSERT INTO product_records VALUES (?, ?, ?, ?)',
                                        (product_id, payload.kind, revisions[0], dumps(binding['levels'])))
                self.connection.execute('INSERT INTO product_geometry VALUES (?, ?)', (product_id, dumps(geometry)))
                self.connection.execute('INSERT INTO product_sources VALUES (?, ?)', (product_id, dumps(sources)))
                if payload.kind == 'change_band':
                    self.connection.executemany('INSERT INTO product_revisions VALUES (?, ?, ?)',
                                                [(product_id, ordinal, revision)
                                                 for ordinal, revision in enumerate(revisions)])
        except sqlite3.Error as exc:
            raise CatalogError(f'cannot publish product {product_id!r}: {exc}') from exc
        return product
