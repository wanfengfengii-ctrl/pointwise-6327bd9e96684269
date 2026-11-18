from dataclasses import dataclass

from terrain.archive.catalog import ArchivedProduct
from terrain.delivery.export import build_package, build_change_package, export_package
from terrain.field.binding import require_same_revision
from terrain.mesh.input import identity
from terrain.mesh.revisions import MeshRevision


@dataclass(frozen=True, slots=True)
class DeliveryView:
    revision: MeshRevision
    products: tuple[ArchivedProduct, ...]
    revisions: tuple[MeshRevision, ...] | None = None

    def __post_init__(self):
        if self.revisions is None:
            object.__setattr__(self, 'revisions', (self.revision,))

    @property
    def is_change_band(self):
        return bool(self.products) and self.products[0].payload.kind == 'change_band'


def read_saved_product(catalog, product_id):
    return catalog.load_product(product_id)


def _change_revisions(products):
    if len(products) != 1 or products[0].payload.kind != 'change_band':
        raise ValueError('change band delivery holds exactly one change band product')
    return products[0].payload.revision_ids


def read_export(catalog, product_ids, destination=None):
    if isinstance(product_ids, str):
        product_ids = (product_ids,)
    ids = tuple(sorted(set(map(identity, product_ids))))
    if not ids:
        raise ValueError('delivery requires at least one saved product')
    products = tuple(read_saved_product(catalog, id_) for id_ in ids)
    kinds = {product.payload.kind for product in products}
    if 'change_band' in kinds:
        revision_ids = _change_revisions(products)
        revisions = tuple(catalog.meshes.get_revision(revision_id) for revision_id in revision_ids)
        view = DeliveryView(revisions[0], products, revisions)
        if destination is not None:
            export_package(build_change_package(revisions, products), destination)
        return view
    revision_id = products[0].revision_id
    require_same_revision(revision_id, *products)
    view = DeliveryView(catalog.meshes.get_revision(revision_id), products)
    if destination is not None:
        export_package(build_package(view.revision, view.products), destination)
    return view
