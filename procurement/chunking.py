"""Splitting a document's text for a chunked extraction pass, and running it.

Lives here rather than inside either extractor because two of them now need
the same rule. A second copy would drift: a fix applied to one splitter would
silently leave the other tearing values from their units, which is the precise
failure the "arithmetic stays in Python" rule exists to prevent.

The same argument carries the budget and the per-chunk ask below: both
extractors read a whole document, both split it the same way, and both merge
all-or-nothing, so the retry that bounds a chunk's failure probability and the
floor that bounds the number of chunks belong here once rather than twice.
"""
import logging
import os

_log = logging.getLogger(__name__)


def chunk_on_lines(text: str, budget: int) -> list[str]:
    """Split on line boundaries, in order, losslessly.

    Never mid-line: read_xlsx_text emits one spreadsheet row per line and
    read_pdf_text preserves line structure, so a value torn from its unit
    invites the model to pair the wrong number with the wrong unit. No overlap:
    overlap duplicates the records the chunk carries.

    A single line longer than `budget` is emitted whole rather than cut - the
    line boundary is the guarantee, and the budget is the target it is kept to
    wherever the text gives it a boundary to keep.
    """
    out: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        cost = len(line) + 1
        if current and size + cost > budget:
            out.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += cost
    if current:
        out.append("\n".join(current))
    return out or [""]


# Below this, a chunk budget is not a tuning choice, it is a typo. chunk_on_lines
# emits at least one line per chunk whatever the budget, so `TECH_CHUNK_CHARS=0`
# - or a negative, or a stray minus - turns a 20,000-line proposal into 20,000
# paid LLM calls, and nothing downstream would notice until the bill arrived.
# The floor is well under every shipped default (8,000 and 12,000) and well over
# any single line the corpus's readers emit, so it constrains only nonsense.
MIN_CHUNK_CHARS = 1000

# One retry, no more, and no backoff sleep. What this exists for is a single
# degenerate response - a model that echoed the prompt's placeholder keys
# instead of answering ("got keys ['$PARAMETER_NAME', '$PARAMETER_VALUE']"), or
# one response check_truncated refused - which a second ask of the same prompt
# usually clears. Because the merge is all-or-nothing, a document's failure
# probability scales with its chunk count: AESL's 220,933-char proposal is ~19
# chunks, i.e. 19 independent chances to lose the whole document. Retrying
# unboundedly would turn a genuinely broken document into an unbounded charge,
# which is the opposite failure.
CHUNK_ATTEMPTS = 2


def budget_from_env(name: str, default: int) -> int:
    """A chunk budget read from the environment, or the shipped default.

    An unusable value - not an integer, or below MIN_CHUNK_CHARS - falls back
    to `default` and says so in the log rather than being honoured. Clamping
    rather than raising at import: this is read at module scope, so a raise
    takes the API down for a typo in one environment variable, while the
    fallback keeps the run going at a budget that is known to work.
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        _log.warning("%s=%r is not an integer; using the default %d",
                     name, raw, default)
        return default
    if value < MIN_CHUNK_CHARS:
        _log.warning("%s=%d is below the %d-char floor and would cost one LLM "
                     "call per line; using the default %d",
                     name, value, MIN_CHUNK_CHARS, default)
        return default
    return value


def ask_each_chunk(client, prompt, schema, chunks: list[str], field: str,
                   suffix: str = "") -> list:
    """Ask `client` for `schema` once per chunk; return every `field` item.

    In chunk order, so a merged list still reads in document order. `suffix` is
    appended to every chunk because it is an instruction rather than document
    content - a chunk asked without it would be asked a different question.

    A chunk that raises is asked again, up to CHUNK_ATTEMPTS times; the last
    attempt's exception propagates unchanged, so the caller's all-or-nothing
    failure path - `return [], "failed", notes`, never a raise, never a blanked
    store - is exactly what it was.

    An omitted optional array is an empty chunk, not a failed one: a tool call
    may legitimately return no `field` at all, and treating that as a failure
    would make a document with one empty page a permanent per-run charge.
    """
    items: list = []
    for position, chunk in enumerate(chunks, 1):
        for attempt in range(1, CHUNK_ATTEMPTS + 1):
            try:
                raw = client.classify_structure(prompt, schema, chunk + suffix)
                break
            except Exception as exc:
                if attempt == CHUNK_ATTEMPTS:
                    raise
                _log.warning("chunk %d/%d failed on attempt %d/%d, retrying: %s",
                             position, len(chunks), attempt, CHUNK_ATTEMPTS, exc)
        items.extend(raw.get(field) or [])
    return items
