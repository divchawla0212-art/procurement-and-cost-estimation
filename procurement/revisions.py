"""Revision-label parsing and supersession resolution.

Real tender packages carry several revisions of the same document
(`…-935.pdf` and `…-935(Rev1).pdf`) and sometimes say so outright
("Superseded with MOM 20241111"). Extracting an obsolete revision would
put withdrawn numbers into the comparison, so lineage is resolved before
anything is extracted.
"""
import os
import re

from procurement.store.models import DocumentRecord

#  No leading \b: revision markers are sometimes concatenated directly onto
# an identifier with no separator (e.g. "935Rev1.pdf") and that must still be
# recognised. The trailing \b is what matters for safety: it is what stops
# "Revenue"/"Reverse" from being read as "Rev" + a single-letter revision
# (their next character is a word character, so no boundary exists there).
_REV = re.compile(r"rev[\s._-]*([0-9]+|[a-z])\b", re.IGNORECASE)
_SUPERSEDED = re.compile(r"supersede", re.IGNORECASE)
# Everything from the "supersede" marker to the end of the name is metadata
# about the supersession event (e.g. "Superseded with MOM 20241111"), not
# part of the document's identity, so it is dropped wholesale when grouping.
_SUPERSEDED_TAIL = re.compile(r"supersede.*$", re.IGNORECASE | re.DOTALL)
# A bare "copy" (OS-generated duplicate marker, e.g. "... copy.pdf") carries
# no identity information either. Word-bounded so "Copyright.pdf" is untouched.
_COPY_MARKER = re.compile(r"\bcopy\b", re.IGNORECASE)
_LEADING_INDEX = re.compile(r"^\s*\d{1,2}[\s._-]+")
_REV_CHUNK = re.compile(r"[\(\[]?\s*rev[\s._-]*(?:[0-9]+|[a-z])\b\s*[\)\]]?", re.IGNORECASE)
_NOISE = re.compile(r"[^a-z0-9]+")


def parse_revision(filename: str) -> str | None:
    """Return the revision label as an upper-case string, or None."""
    match = _REV.search(os.path.basename(filename))
    if match is None:
        return None
    label = match.group(1).upper()
    return str(int(label)) if label.isdigit() else label


def normalised_base(filename: str) -> str:
    """Identity of a document across its revisions: extension, revision
    marker, leading index number and punctuation removed."""
    name = os.path.splitext(os.path.basename(filename))[0]
    name = _LEADING_INDEX.sub("", name)
    name = _REV_CHUNK.sub("", name)
    name = _SUPERSEDED_TAIL.sub("", name)
    name = _COPY_MARKER.sub("", name)
    return _NOISE.sub("", name.lower())


def _rank(doc: DocumentRecord) -> tuple[int, int, str]:
    """Higher sorts newer. An explicit 'superseded' marker always sorts oldest."""
    name = os.path.basename(doc.path)
    if _SUPERSEDED.search(name):
        return (-1, 0, "")
    label = parse_revision(name)
    if label is None:
        return (0, 0, "")
    return (1, int(label), "") if label.isdigit() else (1, 0, label)


def resolve_supersession(docs: list[DocumentRecord]) -> list[DocumentRecord]:
    """Populate revision_label / supersedes / superseded_by across a document set."""
    out = [d.model_copy() for d in docs]
    for doc in out:
        doc.revision_label = parse_revision(os.path.basename(doc.path))

    groups: dict[tuple[str | None, str], list[DocumentRecord]] = {}
    for doc in out:
        groups.setdefault((doc.vendor, normalised_base(doc.path)), []).append(doc)

    for members in groups.values():
        if len(members) < 2:
            continue
        # Oldest first. `superseded_by` always names the newest document (the
        # one a later extraction-skip step should treat as authoritative);
        # `supersedes` is the lineage pointer and must name the immediate
        # predecessor, not just any older sibling, so a 3+ chain reads as a
        # chain rather than skipping members.
        ordered = sorted(members, key=_rank)
        newest = ordered[-1]
        for doc in ordered[:-1]:
            doc.superseded_by = newest.doc_id
        for prev, doc in zip(ordered, ordered[1:]):
            doc.supersedes = prev.doc_id
    return out
