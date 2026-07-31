import os
import base64

_PROMPT = (
    "Transcribe ALL text from this document exactly and completely. Preserve "
    "tables, line items, quantities, prices, currencies, and terms as readable "
    "plain text. Return only the transcribed text, with no commentary."
)


def _build_document_block(pdf_bytes: bytes) -> dict:
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
        },
    }


def transcribe_pdf(path: str, model: str | None = None) -> str:
    """Transcribe a PDF to text using Anthropic's native PDF document input.
    Used only as a fallback when the text layer is empty/thin. Requires
    ANTHROPIC_API_KEY and a PDF-capable model (set PDF_LLM_MODEL if needed)."""
    import anthropic

    model = model or os.getenv("PDF_LLM_MODEL", "claude-sonnet-4-5")
    with open(path, "rb") as fh:
        pdf_bytes = fh.read()
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=8000,
        messages=[{
            "role": "user",
            "content": [
                _build_document_block(pdf_bytes),
                {"type": "text", "text": _PROMPT},
            ],
        }],
    )
    return "".join(b.text for b in message.content if getattr(b, "type", None) == "text")
