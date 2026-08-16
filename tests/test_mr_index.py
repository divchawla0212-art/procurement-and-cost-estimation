"""Passages, and the BM25 index over them — BD-8's retrieval half.

Key-free by construction: there is no model anywhere in this file. BM25 is pure
Python and the store is stdlib `sqlite3`, which is the whole reason the design
picked lexical retrieval over embeddings — see `workflow/mr_index.py`'s module
docstring.

The load-bearing test here is **I-G**, and it is a two-run one:
`test_reissuing_the_mr_leaves_no_passage_of_the_superseded_revision`. `add` is a
wholesale replacement for one RFQ, so a re-issue cannot leave a bidder being
answered out of a revision nobody is bidding against. A single-run test cannot
tell replacement from accumulation — both return the passage it searched for.
"""
import hashlib
import io
import os

import pytest

from workflow import mr_index, passages
from workflow.doc_store import LocalBlobStore
from workflow.models.rfq_document import RfqDocument

# -- fixtures ------------------------------------------------------------------

CABLE_MR = """\
3.4 Cable insulation
The conductor insulation shall be cross-linked polyethylene, XLPE, rated for a
continuous conductor temperature of ninety degrees celsius.

3.5 Armouring
Single core cables shall be armoured with aluminium wire. Multicore cables
shall be armoured with galvanised steel wire.

3.6 Sheath colour
The outer sheath shall be black, with the circuit reference printed at one
metre intervals along its length.
"""


def a_passage(rfq_id: str, ident: str, text: str, ordinal: int = 0) -> passages.Passage:
    return passages.Passage(id=ident, rfq_id=rfq_id, text=text, ordinal=ordinal)


def an_index(tmp_path) -> mr_index.Bm25Index:
    return mr_index.Bm25Index(str(tmp_path))


# -- splitting a document into passages ----------------------------------------


def test_a_document_becomes_more_than_one_passage():
    """A whole MR in one passage retrieves the whole MR for every question,
    which is the same as no retrieval at all."""
    out = passages.split_text("rfq_1", CABLE_MR, document_id="rdoc_a", budget=120)

    assert len(out) > 1
    assert all(p.rfq_id == "rfq_1" for p in out)


def test_a_passage_never_cuts_a_line_in_half():
    """`chunk_on_lines`' rule, kept rather than re-implemented: a value torn
    from its unit invites an answer pairing the wrong number with the wrong
    unit."""
    out = passages.split_text("rfq_1", CABLE_MR, document_id="rdoc_a", budget=120)

    original = CABLE_MR.splitlines()
    for passage in out:
        for line in passage.text.splitlines():
            assert line in original, f"a line was cut: {line!r}"
    for line in original:
        if line.strip():
            assert any(line in p.text for p in out), f"a line was lost: {line!r}"


def test_every_passage_of_a_document_carries_a_distinct_id():
    """Ids are what the model cites and what `supported` is checked against, so
    two passages sharing one would make a citation ambiguous."""
    out = passages.split_text("rfq_1", CABLE_MR, document_id="rdoc_a", budget=120)

    assert len({p.id for p in out}) == len(out)


def test_two_documents_of_one_rfq_never_share_a_passage_id():
    """The citation check is per RFQ, not per document, so the ids have to be
    unique across everything indexed for that RFQ."""
    a = passages.split_text("rfq_1", CABLE_MR, document_id="rdoc_a", budget=120)
    b = passages.split_text("rfq_1", CABLE_MR, document_id="rdoc_b", budget=120)

    assert not ({p.id for p in a} & {p.id for p in b})


def test_splitting_the_same_document_twice_gives_the_same_ids():
    """`mr-index.db` is derived and disposable — it may be deleted and rebuilt
    from the documents on disk at any time. That is only true if rebuilding
    reproduces the ids a stored citation names."""
    first = passages.split_text("rfq_1", CABLE_MR, document_id="rdoc_a", budget=120)
    again = passages.split_text("rfq_1", CABLE_MR, document_id="rdoc_a", budget=120)

    assert [p.id for p in first] == [p.id for p in again]


def test_a_blank_passage_is_dropped():
    """`chunk_on_lines` answers `[""]` for empty text. An empty passage can
    never be retrieved and can never support an answer, so indexing one only
    consumes a slot in `k`."""
    assert passages.split_text("rfq_1", "\n\n   \n", document_id="rdoc_a") == []


def test_a_stored_document_is_read_through_the_blob_store(tmp_path):
    """The path is rebuilt from the record's parts through `doc_store`, never
    trusted as a string somebody wrote into a document once."""
    root = str(tmp_path)
    store = LocalBlobStore(root)
    ref = store.put("rfq_1", "mr.txt", io.BytesIO(CABLE_MR.encode("utf-8")))
    record = RfqDocument(
        id="rdoc_a",
        rfq_id="rfq_1",
        filename="mr.txt",
        rel_path="enquiry/mr.txt",
        sha256=ref.sha256,
        size_bytes=ref.size,
        uploaded_by="buyer@example.com",
        uploaded_at="2026-08-15T09:00:00+00:00",
    )

    out = passages.passages_for_document(root, record, budget=120)

    assert out, "the document's text produced no passages"
    assert all(p.document_id == "rdoc_a" and p.rfq_id == "rfq_1" for p in out)
    assert any("cross-linked polyethylene" in p.text for p in out)


# -- retrieval -----------------------------------------------------------------


def test_a_passage_answering_the_question_is_retrieved(tmp_path):
    """The one thing the index is for."""
    index = an_index(tmp_path)
    index.add("rfq_1", passages.split_text("rfq_1", CABLE_MR, document_id="rdoc_a",
                                           budget=120))

    hits = index.search("rfq_1", "what armouring is required on single core cables?", k=2)

    assert hits, "nothing was retrieved"
    assert "aluminium wire" in hits[0].text


def test_a_hit_carries_the_id_a_citation_is_checked_against(tmp_path):
    index = an_index(tmp_path)
    index.add("rfq_1", [a_passage("rfq_1", "rdoc_a#1", "The sheath shall be black.")])

    hits = index.search("rfq_1", "sheath colour", k=6)

    assert [h.passage_id for h in hits] == ["rdoc_a#1"]
    assert hits[0].rfq_id == "rfq_1"
    assert hits[0].score > 0


def test_k_bounds_how_many_come_back(tmp_path):
    index = an_index(tmp_path)
    index.add("rfq_1", [a_passage("rfq_1", f"rdoc_a#{i}", "cable insulation sheath", i)
                        for i in range(1, 10)])

    assert len(index.search("rfq_1", "cable", k=3)) == 3


def test_a_question_sharing_no_word_with_the_mr_retrieves_nothing(tmp_path):
    """Retrieving the whole package for a question it does not answer would put
    six irrelevant passages in front of the model and invite it to cite one."""
    index = an_index(tmp_path)
    index.add("rfq_1", passages.split_text("rfq_1", CABLE_MR, document_id="rdoc_a",
                                           budget=120))

    assert index.search("rfq_1", "warranty period liquidated damages", k=6) == []


def test_searching_an_rfq_that_was_never_indexed_is_empty_rather_than_an_error(tmp_path):
    """A first run, not a failure — the reading `persistence.load` gives a
    missing document."""
    assert an_index(tmp_path).search("rfq_nothing", "insulation", k=6) == []


def test_one_rfqs_passages_never_answer_another_rfqs_question(tmp_path):
    """Two enquiries for the same equipment have near-identical vocabulary, so
    a missing `rfq_id` filter would answer a bidder out of somebody else's
    package and nothing on screen would look wrong."""
    index = an_index(tmp_path)
    index.add("rfq_1", [a_passage("rfq_1", "rdoc_a#1", "The sheath shall be black.")])
    index.add("rfq_2", [a_passage("rfq_2", "rdoc_b#1", "The sheath shall be orange.")])

    hits = index.search("rfq_2", "sheath colour", k=6)

    assert [h.passage_id for h in hits] == ["rdoc_b#1"]
    assert "orange" in hits[0].text


def test_the_better_match_is_ranked_first(tmp_path):
    """BM25 or not, the ordering is the product: `k` is a cut-off, so a passage
    ranked below it is one the model never sees."""
    index = an_index(tmp_path)
    index.add("rfq_1", [
        a_passage("rfq_1", "rdoc_a#1", "The outer sheath shall be black.", 1),
        a_passage("rfq_1", "rdoc_a#2",
                  "The conductor insulation shall be XLPE rated ninety degrees "
                  "celsius for continuous conductor temperature.", 2),
    ])

    hits = index.search("rfq_1", "conductor insulation temperature rating", k=6)

    assert hits[0].passage_id == "rdoc_a#2"


def test_the_same_search_twice_gives_the_same_order(tmp_path):
    """Deterministic is half of why lexical was chosen. Equal-scoring passages
    are ordered by where they sit in the document, never by whatever order
    SQLite happened to return rows in."""
    index = an_index(tmp_path)
    index.add("rfq_1", [a_passage("rfq_1", f"rdoc_a#{i}", "cable insulation", i)
                        for i in range(1, 6)])

    first = [h.passage_id for h in index.search("rfq_1", "cable insulation", k=5)]
    again = [h.passage_id for h in index.search("rfq_1", "cable insulation", k=5)]

    assert first == again
    assert first == [f"rdoc_a#{i}" for i in range(1, 6)]


def test_a_blank_question_retrieves_nothing_rather_than_everything(tmp_path):
    index = an_index(tmp_path)
    index.add("rfq_1", [a_passage("rfq_1", "rdoc_a#1", "The sheath shall be black.")])

    assert index.search("rfq_1", "   ", k=6) == []


# -- I-G: the index holds exactly the current revision --------------------------


def test_reissuing_the_mr_leaves_no_passage_of_the_superseded_revision(tmp_path):
    """**I-G, and it takes two runs to see.**

    `add` replaces an RFQ's passages wholesale. Accumulating instead would leave
    the superseded revision retrievable, and a bidder would be answered — with a
    citation, so it would read as grounded — out of a document nobody is bidding
    against. Searching once after one `add` cannot tell the two apart: the
    passage searched for comes back either way.
    """
    index = an_index(tmp_path)

    index.add("rfq_1", [a_passage("rfq_1", "rdoc_a#1",
                                  "The outer sheath shall be black.")])
    assert "black" in index.search("rfq_1", "sheath colour", k=6)[0].text

    index.add("rfq_1", [a_passage("rfq_1", "rdoc_b#1",
                                  "The outer sheath shall be orange.")])

    hits = index.search("rfq_1", "sheath colour", k=6)
    assert [h.passage_id for h in hits] == ["rdoc_b#1"]
    assert not any("black" in h.text for h in hits)
    assert index.search("rfq_1", "black", k=6) == []


def test_re_adding_leaves_another_rfqs_passages_alone(tmp_path):
    """The replacement is scoped to one RFQ. Wiping the table would hold I-G
    and destroy every other enquiry's index at the same time."""
    index = an_index(tmp_path)
    index.add("rfq_1", [a_passage("rfq_1", "rdoc_a#1", "The sheath shall be black.")])
    index.add("rfq_2", [a_passage("rfq_2", "rdoc_b#1", "The armour shall be steel.")])

    index.add("rfq_1", [a_passage("rfq_1", "rdoc_a#2", "The sheath shall be grey.")])

    assert [h.passage_id for h in index.search("rfq_2", "armour", k=6)] == ["rdoc_b#1"]


def test_re_adding_nothing_empties_the_rfq(tmp_path):
    """An MR withdrawn and not replaced leaves no passage behind — the same
    reading `save` gives an entity removed in memory."""
    index = an_index(tmp_path)
    index.add("rfq_1", [a_passage("rfq_1", "rdoc_a#1", "The sheath shall be black.")])

    index.add("rfq_1", [])

    assert index.search("rfq_1", "sheath", k=6) == []


def test_two_documents_of_one_rfq_are_indexed_together(tmp_path):
    """An enquiry package is several documents, and a bidder's question is
    answered out of whichever one covers it — so the whole package has to be
    retrievable at once, not merely the last one indexed.

    This is the *intended* call shape, stated as a test because the wrong one is
    silent: `add` replaces per RFQ, so a caller looping over documents and
    calling once each would leave only the last document indexed and answer
    "the package does not say" to questions the package does answer. Nothing
    below this line can be built by calling `add` per document.
    """
    index = an_index(tmp_path)
    mr = passages.split_text("rfq_1", CABLE_MR, document_id="rdoc_mr", budget=120)
    datasheet = passages.split_text(
        "rfq_1", "Item 12: the gland shall be brass, double compression.",
        document_id="rdoc_ds")

    index.add("rfq_1", [*mr, *datasheet])

    from_the_mr = index.search("rfq_1", "outer sheath colour", k=6)
    from_the_datasheet = index.search("rfq_1", "double compression gland", k=6)

    assert from_the_mr and from_the_mr[0].document_id == "rdoc_mr"
    assert from_the_datasheet and from_the_datasheet[0].document_id == "rdoc_ds"


def test_indexing_a_second_document_on_its_own_replaces_the_first(tmp_path):
    """The trap the docstring warns about, pinned as behaviour rather than left
    to be discovered. `add` is a replacement, so this is I-G working exactly as
    specified — and it is also how a caller who loops over documents loses most
    of the package. If this ever stops being true, the warning on `add` is stale
    and the batching rule above is no longer load-bearing."""
    index = an_index(tmp_path)
    index.add("rfq_1", passages.split_text("rfq_1", "The sheath shall be black.",
                                           document_id="rdoc_mr"))
    index.add("rfq_1", passages.split_text("rfq_1", "The gland shall be brass.",
                                           document_id="rdoc_ds"))

    assert index.search("rfq_1", "sheath", k=6) == []
    assert index.search("rfq_1", "gland", k=6)[0].document_id == "rdoc_ds"


def test_a_passage_belonging_to_another_rfq_is_refused(tmp_path):
    """The `rfq_id` argument is what the replacement is scoped to, so a passage
    carrying a different one would be filed under an enquiry it is not part of
    and answer that enquiry's bidders out of somebody else's package. Refused
    with the mismatch named rather than quietly re-filed."""
    with pytest.raises(ValueError) as excinfo:
        an_index(tmp_path).add("rfq_1", [a_passage("rfq_2", "rdoc_b#1", "black")])

    assert "rfq_2" in str(excinfo.value)


def test_a_refused_batch_leaves_the_previous_revision_indexed(tmp_path):
    """The check runs before the first delete. Refusing half way would have
    emptied the RFQ and then raised, which is a worse state than not writing."""
    index = an_index(tmp_path)
    index.add("rfq_1", [a_passage("rfq_1", "rdoc_a#1", "The sheath shall be black.")])

    with pytest.raises(ValueError):
        index.add("rfq_1", [a_passage("rfq_1", "rdoc_a#2", "grey"),
                            a_passage("rfq_2", "rdoc_b#1", "orange")])

    assert [h.passage_id for h in index.search("rfq_1", "sheath", k=6)] == ["rdoc_a#1"]


def test_the_index_survives_a_new_process(tmp_path):
    """Persisted, not in memory: the API restarts on every code change."""
    an_index(tmp_path).add("rfq_1", [a_passage("rfq_1", "rdoc_a#1",
                                               "The sheath shall be black.")])

    hits = mr_index.Bm25Index(str(tmp_path)).search("rfq_1", "sheath", k=6)

    assert [h.passage_id for h in hits] == ["rdoc_a#1"]


def test_the_database_can_be_deleted_and_rebuilt(tmp_path):
    """Derived and disposable, in the sense `index/store.db` is. Nothing here is
    reconstructible only from the index itself."""
    index = an_index(tmp_path)
    index.add("rfq_1", [a_passage("rfq_1", "rdoc_a#1", "The sheath shall be black.")])

    os.remove(mr_index.db_path(str(tmp_path)))
    rebuilt = mr_index.Bm25Index(str(tmp_path))

    assert rebuilt.search("rfq_1", "sheath", k=6) == []
    rebuilt.add("rfq_1", [a_passage("rfq_1", "rdoc_a#1", "The sheath shall be black.")])
    assert [h.passage_id for h in rebuilt.search("rfq_1", "sheath", k=6)] == ["rdoc_a#1"]


def test_the_index_is_written_beside_the_other_stores(tmp_path):
    """`<ROOT>/mr-index.db`, the name the design names."""
    an_index(tmp_path).add("rfq_1", [a_passage("rfq_1", "rdoc_a#1", "black sheath")])

    assert os.path.exists(os.path.join(str(tmp_path), "mr-index.db"))


def test_a_term_repeated_in_the_question_does_not_break_the_search(tmp_path):
    index = an_index(tmp_path)
    index.add("rfq_1", [a_passage("rfq_1", "rdoc_a#1", "The sheath shall be black.")])

    assert index.search("rfq_1", "sheath sheath sheath", k=6)[0].passage_id == "rdoc_a#1"


def test_a_number_is_a_searchable_term(tmp_path):
    """Specification vocabulary is half numbers — "90 degrees", "11 kV". A
    tokeniser that kept only letters would drop exactly the terms a bidder
    quotes back at us."""
    index = an_index(tmp_path)
    index.add("rfq_1", [
        a_passage("rfq_1", "rdoc_a#1", "Rated voltage 11 kV.", 1),
        a_passage("rfq_1", "rdoc_a#2", "Rated voltage 33 kV.", 2),
    ])

    assert index.search("rfq_1", "11", k=6)[0].passage_id == "rdoc_a#1"


def test_indexing_reads_the_passage_ids_it_is_given(tmp_path):
    """The index never invents an id: a citation is checked against what the
    caller indexed, so the caller has to be the one that names them."""
    index = an_index(tmp_path)
    index.add("rfq_1", [a_passage("rfq_1", "an-id-of-my-own", "black sheath")])

    assert index.search("rfq_1", "sheath", k=6)[0].passage_id == "an-id-of-my-own"


def test_a_hash_of_the_text_is_not_what_identifies_a_passage(tmp_path):
    """Two passages with identical text are two passages — an MR that says
    "Not applicable." twice has two of them, and collapsing them would lose one
    of the two places it was said."""
    index = an_index(tmp_path)
    index.add("rfq_1", [
        a_passage("rfq_1", "rdoc_a#1", "Not applicable.", 1),
        a_passage("rfq_1", "rdoc_a#2", "Not applicable.", 2),
    ])

    assert len(index.search("rfq_1", "applicable", k=6)) == 2


def test_the_digest_of_the_stored_bytes_is_what_was_uploaded(tmp_path):
    """Guards the fixture above rather than the index: if `put` and the record
    disagreed about the digest, `passages_for_document` would be reading a path
    that happened to exist."""
    root = str(tmp_path)
    ref = LocalBlobStore(root).put("rfq_1", "mr.txt", io.BytesIO(b"hello"))

    assert ref.sha256 == hashlib.sha256(b"hello").hexdigest()
