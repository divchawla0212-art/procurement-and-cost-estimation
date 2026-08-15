"""A stored document, cut into the pieces a question is answered out of — BD-8.

One step of the BD-8 pipeline and nothing else:

    a stored RfqDocument
      └─► doc_store.blob_ref → LocalBlobStore.path_for   (where the bytes are)
            └─► loaders.read_text_with_source            (what they say)
                  └─► chunking.chunk_on_lines            (in passage-sized pieces)

**The splitter is `procurement.chunking.chunk_on_lines`, not a second one.**
That module exists because two extractors needed the same rule and a second copy
would drift; a third copy here would drift the same way, and its failure would
be the one that rule was written about — a value torn from its unit, so a
passage says *90* where the clause said *90 °C*. The budget differs and the rule
does not.

**No overlap between passages, and that is a judgement rather than an
inheritance.** Overlap would improve recall for a clause that straddles a
boundary, and it costs something real in return: a duplicated sentence is
retrieved twice, so it consumes two of the six slots `k` allows and hands the
model the same text under two ids to cite. Line-boundary splitting at 1 200
characters already puts a whole numbered clause in one passage in the documents
this was written against. If a boundary miss ever shows up in practice, the
place to fix it is here and the fix is overlap — not a second splitter.

**Ids are derived, not generated.** `mr-index.db` is derived and disposable —
the design says it may be deleted and rebuilt from the documents on disk at any
time — and that is only true if rebuilding reproduces the same ids. A `uuid4()`
per passage would make a stored citation unresolvable after any rebuild, which
is the one thing a citation must never be.
"""
from pydantic import BaseModel

from procurement.chunking import chunk_on_lines
from procurement.loaders import read_text_with_source
from workflow.doc_store import LocalBlobStore, blob_ref
from workflow.models.rfq_document import RfqDocument

#: Target size of one passage, in characters. Six of these is roughly 7 000
#: characters in front of the model, which is one comfortable call; smaller
#: would cut clauses apart, and much larger dilutes the term-overlap signal
#: BM25 ranks on until a whole page scores for one matching word.
PASSAGE_CHARS = 1200


class Passage(BaseModel):
    """One retrievable piece of one document.

    `id` is what the model cites and what `supported` is checked against, so it
    has to be unique across everything indexed for an RFQ — not merely within
    the document it came from. `document_id` and `ordinal` are what make it
    unique and what let a reader find the passage again in the file it came out
    of.
    """

    id: str
    rfq_id: str
    text: str
    document_id: str | None = None
    ordinal: int = 0


def passage_id(document_id: str | None, ordinal: int) -> str:
    """`rdoc_ab12#3` — the document, and where in it.

    Short enough for a model to echo back exactly, which is the whole job: a
    citation is only checkable if it can be reproduced character for character.
    """
    return f"{document_id or 'doc'}#{ordinal}"


def split_text(
    rfq_id: str,
    text: str,
    *,
    document_id: str | None = None,
    budget: int = PASSAGE_CHARS,
) -> list[Passage]:
    """Passages, in document order, numbered from 1.

    A chunk with nothing but whitespace in it is dropped rather than indexed:
    `chunk_on_lines` answers `[""]` for empty text, and an empty passage can
    never be retrieved and can never support an answer — indexing one only
    spends a slot in `k`. The ordinal counts the passages that were kept, so
    the ids stay dense and reproduce exactly on a rebuild.
    """
    out: list[Passage] = []
    for chunk in chunk_on_lines(text, budget):
        if not chunk.strip():
            continue
        ordinal = len(out) + 1
        out.append(Passage(
            id=passage_id(document_id, ordinal),
            rfq_id=rfq_id,
            text=chunk,
            document_id=document_id,
            ordinal=ordinal,
        ))
    return out


def passages_for_document(
    root: str,
    document: RfqDocument,
    *,
    budget: int = PASSAGE_CHARS,
) -> list[Passage]:
    """Read one stored document off disk and cut it up.

    The path is rebuilt from the record's parts through `doc_store.blob_ref`
    rather than taken from a field, so what is opened is derived from validated
    leaves on every call — the rule that module's docstring states, kept by its
    callers rather than only by itself.

    Whatever `read_text_with_source` raises is raised: a `.doc` it refuses, a
    missing file, a reader that fails. Swallowing it would index the document as
    empty, and an MR that indexes as empty answers every question with "the
    package does not say" — an absence manufactured out of a read failure, which
    is the distinction this whole feature is built around.
    """
    ref = blob_ref(document.rfq_id, document.sha256, document.size_bytes,
                   document.filename)
    text, _reader = read_text_with_source(LocalBlobStore(root).path_for(ref))
    return split_text(document.rfq_id, text, document_id=document.id, budget=budget)
