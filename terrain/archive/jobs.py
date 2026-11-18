from dataclasses import dataclass
from fractions import Fraction

from terrain.archive.catalog import ArchivedProduct, require_product_binding
from terrain.archive.codec import ProductPayload
from terrain.band.clipping import clip_band_cells, validate_interval
from terrain.band.components import assemble_band_result
from terrain.band.sectors import build_band_sectors
from terrain.contour.assembly import assemble_contours
from terrain.contour.incidence import build_level_incidence
from terrain.field.binding import bind_field
from terrain.mesh.input import identity
from terrain.mesh.revisions import MeshRevision


@dataclass(frozen=True, slots=True)
class ProcessingJob:
    request: str
    kind: str
    revision: MeshRevision
    levels: tuple[Fraction, ...]


def bind_processing_request(catalog, request, kind, revision_id, levels):
    identity(request)
    identity(revision_id)
    previous = catalog.find_request(request)
    if previous is not None:
        return require_product_binding(previous, kind, revision_id, levels)
    return ProcessingJob(request, kind, catalog.meshes.get_revision(revision_id), levels)


def _publish(catalog, job, result):
    payload = ProductPayload(job.kind, job.revision.revision_id, job.levels, result)
    return catalog.publish_product(ArchivedProduct(job.request, payload))


def run_contour_job(catalog, job):
    field = bind_field(job.revision)
    results = tuple(assemble_contours(build_level_incidence(field, value)) for value in job.levels)
    return _publish(catalog, job, results)


def run_band_job(catalog, job):
    field = bind_field(job.revision)
    interval = validate_interval(field, *job.levels)
    lower, upper = (build_level_incidence(field, value) for value in job.levels)
    cells = clip_band_cells(field, interval, lower, upper)
    graph = build_band_sectors(field, cells, upper)
    return _publish(catalog, job, assemble_band_result(interval, graph))
