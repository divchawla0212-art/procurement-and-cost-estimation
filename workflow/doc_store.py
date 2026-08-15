"""Content-addressed blob storage for RFQ documents.

    <ROOT>/rfq-docs/<rfq_id>/<sha256[:2]>/<sha256>/<original filename>

Content-addressed under a two-character fan-out. Uploading the same bytes under
the same name writes once. The original filename is kept as the leaf so a human
browsing the directory sees names rather than hashes — and two different files
with the same name cannot collide, because they differ before the leaf.

Modelled on `procurement/store/layout.py`, and it keeps that module's rule:
**stdlib only, and no imports from `workflow.*`**, so it cannot become
circular. That rule is also what decides the shape of the guard here. A blob
path is built out of three *leaves* — an RFQ id, a digest and a file name — and
every one of them is refused if it carries a separator, so containment holds by
construction rather than by a second path-resolution check. The one traversal
guard in this repository is `workflow/safe_extract.py`, and callers run
caller-supplied paths through it before they get here; a copy of it in this
module would be the second copy that rule exists to prevent.

`BlobRef.rel_path` is **storage-relative, never absolute**, so the same record
is still valid after the root moves and so a future S3 implementation has
somewhere to put a key. S3 is deliberately not built: the `BlobStore` protocol
exists so that adding it is a new class and a config switch, and building it now
would mean a boto3 dependency, a credential path and a CI story for a
requirement nobody has yet.
"""
import hashlib
import os
import uuid
from dataclasses import dataclass
from typing import BinaryIO, Protocol

#: The one directory this module owns, under the platform root.
DOCS_DIRNAME = "rfq-docs"

_READ_CHUNK = 1 << 20


@dataclass(frozen=True)
class BlobRef:
    """Where a blob is, and what it is.

    `rel_path` is relative to the platform root and always forward-slash
    separated, so it reads the same on every platform and can be stored as-is.
    """

    sha256: str
    size: int
    rel_path: str


class BlobStore(Protocol):
    """What a document store has to be able to do.

    Three operations, because that is all the workflow needs: put the bytes
    somewhere, read them back, and drop them when the last record referencing
    them is gone.
    """

    def put(self, rfq_id: str, filename: str, data: BinaryIO) -> BlobRef: ...

    def open(self, ref: BlobRef) -> BinaryIO: ...

    def delete(self, ref: BlobRef) -> None: ...


def _leaf(value: str, what: str) -> str:
    """One path segment, or `ValueError`.

    Not a traversal check — a traversal check resolves paths and asks where
    they landed, and `workflow/safe_extract.py` is the one place that does
    that. This asks a narrower question: is this string a single name? A blob
    path is assembled from three of them, so an answer of "yes" three times is
    what makes the result provably inside the RFQ's own directory.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Unsafe {what}: the name is empty")
    if value in (".", ".."):
        raise ValueError(f"Unsafe {what}: {value} is not a file name")
    if "/" in value or "\\" in value or "\0" in value:
        raise ValueError(f"Unsafe {what}: {value} is not a single path segment")
    if os.path.splitdrive(value)[0]:
        raise ValueError(f"Unsafe {what}: {value} names a drive")
    return value


def docs_root(root: str) -> str:
    return os.path.join(root, DOCS_DIRNAME)


def rfq_dir(root: str, rfq_id: str) -> str:
    return os.path.join(docs_root(root), _leaf(rfq_id, "RFQ id"))


def blob_ref(rfq_id: str, sha256: str, size: int, filename: str) -> BlobRef:
    """A reference built from its parts rather than read back off a record.

    Callers holding a stored `RfqDocument` rebuild the reference through here,
    so the path they act on is derived from validated leaves every time rather
    than trusted as a string somebody wrote into a document once.
    """
    return BlobRef(
        sha256=sha256,
        size=size,
        rel_path="/".join([
            DOCS_DIRNAME,
            _leaf(rfq_id, "RFQ id"),
            _leaf(sha256, "digest")[:2],
            _leaf(sha256, "digest"),
            _leaf(filename, "file name"),
        ]),
    )


class LocalBlobStore:
    """The only implementation built: blobs as files under the platform root."""

    def __init__(self, root: str) -> None:
        self._root = root

    def path_for(self, ref: BlobRef) -> str:
        return os.path.join(self._root, *ref.rel_path.split("/"))

    def put(self, rfq_id: str, filename: str, data: BinaryIO) -> BlobRef:
        """Store `data` and return where it went.

        The digest is not known until the bytes have been read, so they land in
        a temporary file inside the RFQ's own directory while being hashed and
        are renamed into place afterwards — the same shape as
        `layout.atomic_write_json`, and for the same reason: an interrupted
        upload must not leave a half-written file under a name that claims to
        be the hash of its whole contents. The temp name is unique per call,
        never derived from the destination, so two concurrent uploads of the
        same file cannot truncate each other's.

        Idempotent by content: if the destination already exists, the bytes are
        already stored and the temporary copy is dropped rather than replacing
        an identical file.
        """
        directory = rfq_dir(self._root, rfq_id)
        os.makedirs(directory, exist_ok=True)
        scratch = os.path.join(
            directory, f".incoming.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        digest = hashlib.sha256()
        size = 0
        try:
            with open(scratch, "wb") as out:
                while chunk := data.read(_READ_CHUNK):
                    digest.update(chunk)
                    size += len(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            ref = blob_ref(rfq_id, digest.hexdigest(), size, filename)
            destination = self.path_for(ref)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            if os.path.exists(destination):
                os.remove(scratch)
            else:
                os.replace(scratch, destination)
        except BaseException:
            if os.path.exists(scratch):
                os.remove(scratch)
            raise
        return ref

    def open(self, ref: BlobRef) -> BinaryIO:
        return open(self.path_for(ref), "rb")

    def delete(self, ref: BlobRef) -> None:
        """Drop one blob, and any directory the drop left empty.

        A blob that is already gone is not an error: the record is the
        authority on what exists, and a missing file is the state the caller
        was asking for. The empty-directory sweep stops the fan-out filling
        with husks that make a human browsing the tree think documents are
        there when the records are gone.
        """
        path = self.path_for(ref)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        directory = os.path.dirname(path)
        stop = os.path.abspath(docs_root(self._root))
        while os.path.abspath(directory) != stop:
            try:
                os.rmdir(directory)
            except OSError:
                # Not empty, or gone already. Either way there is nothing
                # above it left to prune.
                break
            directory = os.path.dirname(directory)
