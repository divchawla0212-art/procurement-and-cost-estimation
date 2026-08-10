"""The LlamaParse arm of the parser A/B.

Kept in its own module, imported lazily by `loaders._llamaparse`, because
`llama-cloud` is an optional extra: CI installs the core dependency set and
must stay key-free, and `loaders` sits on the import path of the entire
pipeline. Someone reading a .docx on a clean checkout must not need a hosted
parser to be installed.

Three notes on the SDK, because the ecosystem moved and the obvious package is
the wrong one. `llama-parse` is deprecated. `llama-cloud-services`, which it
pulls in, is *also* deprecated and its maintenance window closed on 2026-05-01.
The maintained package is `llama-cloud>=1.0`, whose entry point is
`LlamaCloud`, and whose parse call is upload-then-parse rather than the old
`LlamaParse(...).load_data(path)`.
"""
import hashlib
import os

# The tier the experiment runs. LlamaParse offers fast (rule-based),
# cost_effective, agentic and agentic_plus; `agentic` is the accurate per-page
# tier the design selected, and the corpus is two table-heavy engineering
# documents, which is the case the cheap rule-based tier handles worst.
DEFAULT_TIER = "agentic"

TIER_ENV = "LLAMAPARSE_TIER"
CACHE_DIR_ENV = "LLAMAPARSE_CACHE_DIR"
DEFAULT_CACHE_DIR = ".llamaparse-cache"

API_KEY_ENV = "LLAMA_CLOUD_API_KEY"


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_path(path: str, tier: str) -> str:
    """Where a parse of this exact file at this tier is remembered.

    Keyed by content hash and tier, never by filename: the A/B copies the same
    corpus into a fresh project directory per arm, so the path changes on every
    run while the bytes do not. Keying on the name would miss every time and
    re-bill every run; keying on content alone would serve a `fast` parse to
    the `agentic` arm.
    """
    root = os.getenv(CACHE_DIR_ENV) or DEFAULT_CACHE_DIR
    return os.path.join(root, f"{_sha256(path)}.{tier}.md")


def parse_pdf(path: str, tier: str | None = None) -> str:
    """One PDF, parsed to markdown by LlamaParse.

    Markdown rather than plain text: it renders table rows as pipe-delimited
    lines, which is both the shape `chunking.py` splits on and the shape
    `read_xlsx_text` gives the control document. Asking this arm for flat text
    would discard the table reconstruction that is the reason to test it.

    Raises rather than returning "" when it is not configured. An unconfigured
    arm that returned an empty string would be indistinguishable from a parser
    that read the document and found nothing, and the experiment would record
    that as LlamaParse losing.
    """
    tier = tier or os.getenv(TIER_ENV) or DEFAULT_TIER

    # Cache first, so a re-run neither re-bills nor re-introduces parser-side
    # variance, and so a cached corpus can be re-scored without a key at all.
    cached = cache_path(path, tier)
    if os.path.exists(cached):
        with open(cached, encoding="utf-8") as fh:
            return fh.read()

    if not os.getenv(API_KEY_ENV):
        raise RuntimeError(
            f"the llamaparse reader needs {API_KEY_ENV} in the environment "
            f"(no cached parse for {os.path.basename(path)} at tier {tier!r})")

    try:
        from llama_cloud import LlamaCloud
    except ImportError as exc:      # pragma: no cover - depends on the extra
        raise RuntimeError(
            "the llamaparse reader needs the optional dependency: "
            "pip install 'llama-cloud>=1.0'. Note that `llama-parse` and "
            "`llama-cloud-services` are both deprecated.") from exc

    client = LlamaCloud()           # reads LLAMA_CLOUD_API_KEY itself
    with open(path, "rb") as fh:
        uploaded = client.files.create(file=fh, purpose="parse")
    # `expand` is required and must be a non-empty sequence: without it the
    # call returns a job handle whose `.markdown` is unpopulated rather than
    # raising, so the arm would silently read every document as empty.
    result = client.parsing.parse(tier=tier, version="latest",
                                  file_id=uploaded.id, expand=["markdown"])
    text = getattr(result, "markdown", None) or ""
    if not text.strip():
        raise RuntimeError(
            f"LlamaParse returned no markdown for {os.path.basename(path)} at "
            f"tier {tier!r}; refusing to cache an empty parse")

    os.makedirs(os.path.dirname(cached) or ".", exist_ok=True)
    with open(cached, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text
