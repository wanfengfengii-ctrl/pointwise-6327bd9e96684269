from dataclasses import dataclass

from terrain.archive.catalog import ArchivedProduct
from terrain.delivery.export import build_package, export_package
from terrain.field.binding import require_same_revision
from terrain.mesh.input import identity
from terrain.mesh.revisions import MeshRevision


@dataclass(frozen=True, slots=True)
class DeliveryView:
    revision: MeshRevision
    products: tuple[ArchivedProduct, ...]


def read_saved_product(catalog, product_id):
    return catalog.load_product(product_id)


def read_export(catalog, product_ids, destination=None):
    if isinstance(product_ids, str):
        product_ids = (product_ids,)
    ids = tuple(sorted(set(map(identity, product_ids))))
    if not ids:
        raise ValueError('delivery requires at least one saved product')
    products = tuple(read_saved_product(catalog, id_) for id_ in ids)
    revision_id = products[0].revision_id
    require_same_revision(revision_id, *products)
    view = DeliveryView(catalog.meshes.get_revision(revision_id), products)
    if destination is not None:
        export_package(build_package(view.revision, view.products), destination)
    return view
