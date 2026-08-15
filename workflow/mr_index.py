"""`<ROOT>/mr-index.db`: retrievable passages of an enquiry package — BD-8.

**Lexical, not embeddings, and that is a constraint rather than a preference.**
`CLAUDE.md` requires the test suite to be key-free and CI runs with no provider
secrets. Anthropic offers no embedding endpoint at all, so "reuse the provider we
already have" is not available; the alternatives are a second provider (a key in
CI, or a skip guard over this whole feature) or a local sentence-transformers
model (a large dependency and a model download in CI). BM25 over tokenised
passages needs neither — it is pure Python and stdlib `sqlite3`, it is
deterministic, and for the query shape here, a vendor quoting specification
vocabulary back at us, term overlap is a strong signal.

`PassageIndex` is the interface that matters. An `EmbeddingIndex` implementing
the same two methods is a config line later; it is deliberately not built now.

**This database is derived and disposable**, in exactly the sense
`index/store.db` is — delete the file and re-index from the documents on disk
and nothing is lost. Nothing is stored here that is not reconstructible that
way: no ids of its own (the caller names the passages, and `workflow/passages.py`
derives those names so a rebuild reproduces them), no timestamps, no counters.

**`add` replaces, and that is invariant I-G.** After `add(rfq_id, passages)` the
index holds exactly those passages for that RFQ and nothing else — so an MR
re-issued at a new revision leaves no passage of the superseded one behind. The
alternative is not a stale row nobody reads: it is a bidder answered, *with a
citation*, out of a document nobody is bidding against, which reads on screen
exactly like a grounded answer. Only a two-run test tells replacement from
accumulation, and `test_reissuing_the_mr_leaves_no_passage_of_the_superseded_revision`
is that test. The replacement is scoped to one RFQ: emptying the table would
hold I-G too, and destroy every other enquiry's index doing it.

Two tables rather than one with the terms in a text column, for the reason
`bidder_db` gives: a list in a text column can only be matched with `LIKE
'%…%'`, the substring matching this repository has twice recorded as a defect.
A row per term makes the lookup an equality test an index can serve, which is
also the only thing that makes scoring cheap — a search touches the passages
that contain a query term, never the whole package.
"""
import math
import os
import re
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import Protocol

from pydantic import BaseModel

from workflow.passages import Passage

SCHEMA = """
CREATE TABLE IF NOT EXISTS passages (
    rfq_id      TEXT NOT NULL,
    passage_id  TEXT NOT NULL,
    document_id TEXT,
    ordinal     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    length      INTEGER NOT NULL,
    PRIMARY KEY (rfq_id, passage_id)
);

CREATE TABLE IF NOT EXISTS passage_terms (
    rfq_id     TEXT NOT NULL,
    passage_id TEXT NOT NULL,
    term       TEXT NOT NULL,
    tf         INTEGER NOT NULL,
    PRIMARY KEY (rfq_id, passage_id, term)
);

CREATE INDEX IF NOT EXISTS ix_passage_terms_lookup
    ON passage_terms (rfq_id, term);
"""

#: The standard BM25 defaults, and there is no tuning here to defend: `k1`
#: bounds how much a term repeated within one passage keeps helping, and `b`
#: how hard a long passage is penalised for its length. 1.5 / 0.75 is what
#: Lucene, Elasticsearch and the original TREC work all use, and picking
#: anything else would be a number nobody could justify against a corpus this
#: feature has not been measured on yet.
K1 = 1.5
B = 0.75

#: Letters and digits, unicode-aware, underscore excluded. Digits are kept
#: deliberately: specification vocabulary is half numbers — "11 kV", "90 °C",
#: "IEC 60502" — and those are exactly the terms a bidder quotes back at us.
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def db_path(root: str) -> str:
    return os.path.join(root, "mr-index.db")


def tokenize(text: str) -> list[str]:
    """Case-folded terms, in order.

    No stemming and no stop list. Stemming would need a language guess and a
    dependency; the stop list BM25 does not need, because a term appearing in
    every passage earns an idf of almost nothing on its own.
    """
    return _TOKEN.findall(text.casefold())


class Hit(BaseModel):
    """One retrieved passage. `passage_id` is what a citation is checked
    against, and `text` is what the model is shown and what a reader checks the
    citation with."""

    passage_id: str
    rfq_id: str
    text: str
    score: float
    document_id: str | None = None
    ordinal: int = 0


class PassageIndex(Protocol):
    """What `clarification_answers.draft_answer` needs, and no more.

    Two methods, so swapping `Bm25Index` for an embedding-backed one later is a
    config line rather than a rewrite of the caller.
    """

    def add(self, rfq_id: str, passages: Iterable[Passage]) -> None: ...

    def search(self, rfq_id: str, query: str, k: int) -> list[Hit]: ...


@contextmanager
def _connect(root: str) -> Iterator[sqlite3.Connection]:
    """A connection with the schema in place.

    `os.makedirs` because the root may not exist on a first run — the same
    reading `persistence.load` gives a missing document: a first run, not an
    error. The schema is re-applied on every connection so that deleting the
    file and carrying on works, which is what "disposable" has to mean in
    practice.
    """
    os.makedirs(root, exist_ok=True)
    conn = sqlite3.connect(db_path(root))
    try:
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        yield conn
    finally:
        conn.close()


class Bm25Index:
    """The only implementation built: BM25 over passages in SQLite."""

    def __init__(self, root: str) -> None:
        self._root = root

    # -- writing ---------------------------------------------------------------

    def add(self, rfq_id: str, passages: Iterable[Passage]) -> None:
        """Make the index hold **exactly** `passages` for `rfq_id`.

        Named `add` because that is the protocol's name, and a wholesale
        replacement because that is invariant I-G — see the module docstring.
        Wholesale rather than a diff for the reason `bidder_db.replace_all`
        gives: a diff needs change tracking on a model that has none, and the
        rows are cheap to rewrite.

        Both deletes are explicit rather than a foreign-key cascade. `PRAGMA
        foreign_keys` is **off by default** in SQLite, so a cascade would make
        the invariant depend on a pragma somebody could omit, and the failure —
        term rows of a superseded revision still scoring — would be silent.

        One transaction, so a failure part-way leaves the previous revision
        indexed rather than half of the new one. Half an MR would answer
        questions with a citation and no way to tell it was half.
        """
        rows = list(passages)
        for passage in rows:
            if passage.rfq_id != rfq_id:
                raise ValueError(
                    f"passage {passage.id!r} belongs to {passage.rfq_id!r}, not "
                    f"{rfq_id!r}; indexing it here would answer one enquiry out "
                    f"of another's package")

        with _connect(self._root) as conn:
            with conn:
                conn.execute("DELETE FROM passage_terms WHERE rfq_id = ?", (rfq_id,))
                conn.execute("DELETE FROM passages WHERE rfq_id = ?", (rfq_id,))
                for passage in rows:
                    terms = tokenize(passage.text)
                    conn.execute(
                        "INSERT INTO passages"
                        " (rfq_id, passage_id, document_id, ordinal, text, length)"
                        " VALUES (?,?,?,?,?,?)",
                        (rfq_id, passage.id, passage.document_id, passage.ordinal,
                         passage.text, len(terms)),
                    )
                    counted: dict[str, int] = {}
                    for term in terms:
                        counted[term] = counted.get(term, 0) + 1
                    conn.executemany(
                        "INSERT INTO passage_terms (rfq_id, passage_id, term, tf)"
                        " VALUES (?,?,?,?)",
                        [(rfq_id, passage.id, term, tf) for term, tf in counted.items()],
                    )

    # -- reading ---------------------------------------------------------------

    def search(self, rfq_id: str, query: str, k: int) -> list[Hit]:
        """The `k` best-matching passages of one RFQ, best first.

        Scoped to `rfq_id` in every query. Two enquiries for the same equipment
        have near-identical vocabulary, so a missing filter would answer a
        bidder out of somebody else's package and nothing on screen would look
        wrong.

        A passage matching no query term is not returned at all, rather than
        returned with a score of zero: `k` irrelevant passages in front of the
        model is an invitation to cite one, and an empty result is the honest
        answer that the package does not discuss this.

        Deterministic, including in a tie: equal scores are ordered by where
        the passages sit in the document, never by whatever order SQLite
        happened to return rows in.
        """
        terms = sorted(set(tokenize(query)))
        # A bidder repeating a word does not make a passage twice as relevant,
        # so the query's own term frequencies are deliberately dropped.
        if not terms or k <= 0:
            return []

        with _connect(self._root) as conn:
            total, avgdl = self._corpus(conn, rfq_id)
            if not total:
                return []

            scored = self._score(conn, rfq_id, terms, total, avgdl)
            best = sorted(scored, key=lambda s: (-s[1], s[2], s[0]))[:k]
            if not best:
                return []
            return self._hits(conn, rfq_id, best)

    @staticmethod
    def _corpus(conn: sqlite3.Connection, rfq_id: str) -> tuple[int, float]:
        row = conn.execute(
            "SELECT count(*) AS n, coalesce(avg(length), 0) AS avgdl"
            " FROM passages WHERE rfq_id = ?", (rfq_id,)).fetchone()
        # A package of nothing but blank pages would give an average of zero and
        # a division by it; those passages are dropped before they get here, and
        # this keeps the arithmetic total rather than relying on that.
        return row["n"], row["avgdl"] or 1.0

    @staticmethod
    def _score(
        conn: sqlite3.Connection,
        rfq_id: str,
        terms: Sequence[str],
        total: int,
        avgdl: float,
    ) -> list[tuple[str, float, int]]:
        """`(passage_id, score, ordinal)` for every passage matching a term.

        One query for the matching rows, not one per passage. The text is
        deliberately not selected here — a term matching four hundred passages
        would haul four hundred passages of prose back to score them and then
        discard all but `k`.
        """
        marks = ",".join("?" * len(terms))
        rows = conn.execute(
            f"SELECT t.passage_id, t.term, t.tf, p.length, p.ordinal"
            f" FROM passage_terms t"
            f" JOIN passages p ON p.rfq_id = t.rfq_id AND p.passage_id = t.passage_id"
            f" WHERE t.rfq_id = ? AND t.term IN ({marks})",
            (rfq_id, *terms),
        ).fetchall()

        df: dict[str, int] = {}
        for row in rows:
            df[row["term"]] = df.get(row["term"], 0) + 1

        scores: dict[str, float] = {}
        ordinals: dict[str, int] = {}
        for row in rows:
            # The non-negative (Lucene) idf. The textbook form goes negative for
            # a term in more than half the passages, which would let a common
            # word *subtract* from a passage's score — so a passage containing
            # every query term could rank below one containing fewer.
            idf = math.log(1 + (total - df[row["term"]] + 0.5) / (df[row["term"]] + 0.5))
            tf = row["tf"]
            norm = tf + K1 * (1 - B + B * row["length"] / avgdl)
            scores[row["passage_id"]] = scores.get(row["passage_id"], 0.0) + (
                idf * tf * (K1 + 1) / norm)
            ordinals[row["passage_id"]] = row["ordinal"]
        return [(pid, score, ordinals[pid]) for pid, score in scores.items()]

    @staticmethod
    def _hits(
        conn: sqlite3.Connection,
        rfq_id: str,
        best: Sequence[tuple[str, float, int]],
    ) -> list[Hit]:
        marks = ",".join("?" * len(best))
        rows = {
            row["passage_id"]: row
            for row in conn.execute(
                f"SELECT passage_id, text, document_id, ordinal FROM passages"
                f" WHERE rfq_id = ? AND passage_id IN ({marks})",
                (rfq_id, *[b[0] for b in best]),
            )
        }
        return [
            Hit(
                passage_id=passage_id,
                rfq_id=rfq_id,
                text=rows[passage_id]["text"],
                score=score,
                document_id=rows[passage_id]["document_id"],
                ordinal=rows[passage_id]["ordinal"],
            )
            for passage_id, score, _ordinal in best
        ]
