import pytest
from pydantic import ValidationError
from shared.provenance import ProvenanceRef


def test_provenance_holds_cell_location():
    ref = ProvenanceRef(document_path="a.xlsx", sheet="S1", cell="K12", extractor="xlsx")
    assert ref.cell == "K12"
    assert ref.prompt_version is None


def test_provenance_is_frozen():
    ref = ProvenanceRef(document_path="a.xlsx", extractor="xlsx")
    with pytest.raises(ValidationError):
        ref.cell = "A1"
