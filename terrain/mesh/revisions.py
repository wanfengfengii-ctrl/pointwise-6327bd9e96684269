import json
import sqlite3
from dataclasses import dataclass

from terrain.mesh.incidence import MeshIncidence, build_mesh_incidence
from terrain.mesh.input import MeshInput, MeshInputError, identity, mesh_document, mesh_fingerprint, normalize_mesh_input
from terrain.mesh.validation import validate_mesh


class RevisionConflict(ValueError):
    pass


class UnknownRevision(LookupError):
    pass


class MeshStoreError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MeshRevision:
    revision_id: str
    mesh: MeshInput
    incidence: MeshIncidence


def revision_document(revision):
    return {'revision': revision.revision_id, 'mesh': mesh_document(revision.mesh),
            'edges': [[*edge, [use.face for use in uses]]
                      for edge, uses in revision.incidence.edges.items()]}


def restore_revision(document, *, verify_geometry=False):
    mesh = normalize_mesh_input(document['mesh']['vertices'], document['mesh']['faces'])
    if verify_geometry:
        validate_mesh(mesh)
    revision = MeshRevision(mesh_fingerprint(mesh), mesh, build_mesh_incidence(mesh))
    if document != revision_document(revision):
        raise MeshInputError('revision snapshot, incidence or fingerprint mismatch')
    return revision


class MeshRevisionStore:
    def __init__(self, connection):
        self.connection = connection

    def initialize(self):
        with self.connection:
            self.connection.execute('''CREATE TABLE IF NOT EXISTS mesh_revisions (
                revision_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)''')
            self.connection.execute('''CREATE TABLE IF NOT EXISTS mesh_requests (
                request_id TEXT PRIMARY KEY,
                revision_id TEXT NOT NULL REFERENCES mesh_revisions(revision_id))''')

    def find_request(self, request):
        row = self.connection.execute('SELECT revision_id FROM mesh_requests WHERE request_id = ?',
                                      (request,)).fetchone()
        return row[0] if row else None

    def get_revision(self, revision_id):
        identity(revision_id)
        row = self.connection.execute('SELECT snapshot FROM mesh_revisions WHERE revision_id = ?',
                                      (revision_id,)).fetchone()
        if row is None:
            raise UnknownRevision(revision_id)
        try:
            revision = restore_revision(json.loads(row[0]))
            if revision.revision_id != revision_id:
                raise MeshInputError('stored revision identity mismatch')
            return revision
        except (ValueError, KeyError, TypeError) as exc:
            raise MeshStoreError(f'invalid saved snapshot for {revision_id!r}') from exc

    def publish(self, request, revision):
        snapshot = json.dumps(revision_document(revision), separators=(',', ':'), sort_keys=True)
        try:
            with self.connection:
                self.connection.execute('BEGIN IMMEDIATE')
                previous = self.find_request(request)
                if previous is not None:
                    if previous != revision.revision_id:
                        raise RevisionConflict(request)
                    return self.get_revision(previous)
                self.connection.execute('''INSERT INTO mesh_revisions VALUES (?, ?)
                    ON CONFLICT(revision_id) DO NOTHING''', (revision.revision_id, snapshot))
                self.connection.execute('INSERT INTO mesh_requests VALUES (?, ?)',
                                        (request, revision.revision_id))
        except sqlite3.Error as exc:
            raise MeshStoreError(f'cannot publish mesh request {request!r}: {exc}') from exc
        return revision


def put_mesh(store, request, vertices, faces):
    identity(request)
    mesh = normalize_mesh_input(vertices, faces)
    revision_id = mesh_fingerprint(mesh)
    previous = store.find_request(request)
    if previous is not None:
        if previous != revision_id:
            raise RevisionConflict(request)
        return store.get_revision(previous)
    validate_mesh(mesh)
    return store.publish(request, MeshRevision(revision_id, mesh, build_mesh_incidence(mesh)))
