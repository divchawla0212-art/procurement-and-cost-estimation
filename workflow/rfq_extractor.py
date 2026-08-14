"""Reading an RFQ document into the fields the raise-an-RFQ form asks for.

There is no model behind this yet. `extract_rfq_info` ignores both the filename
and the bytes and returns the same four fields every time, so that the upload
path — file picker to route to filled-in form — can be built, wired and tested
before a provider is chosen. When one is, this is the single function that
changes; nothing above it knows the answer is invented.

It is deliberately a fixture with an HTTP door on it rather than a bad
extractor. Two things about what it returns follow from that:

**The reference is prefixed `MOCK-`.** A value from here can reach a real RFQ
record if someone accepts the auto-fill without editing it, and this repository
has a standing rule that a synthesised fact must not be indistinguishable on
screen from a recorded one.

**The discipline is a real product group description**, quoted from
`workflow/disciplines.py`. The form's discipline field is a controlled
vocabulary now, so a plausible-sounding `Mechanical` would arrive marked "not a
listed discipline" and the auto-fill would look broken in exactly the
demonstration it exists for. `test_rfq_extractor.py` holds the two together.
"""


def extract_rfq_info(filename: str, content: bytes) -> dict:
    """The RFQ fields a document would yield, were anything reading it.

    Takes the name and the bytes because the real implementation will need
    both — the name carries the file type, and a router that only passed the
    content would have to be changed again to add it back.
    """
    return {
        "reference": "MOCK-RFQ-1234",
        "package": "Gas genset package",
        "discipline": "GENERATOR POWER-GAS TURBINE DRIVEN",
        "value_estimate_aed": 500000,
    }
