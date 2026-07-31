from procurement.classify import (classify_by_rules, classify_document,
                                  CLASSIFY_PROMPT_VERSION)
from shared.llm.mock_client import MockLLMClient


def test_rules_win_and_the_model_is_never_called():
    client = MockLLMClient(response={"doc_class": "drawing"})
    assert classify_document("/p/vendors/K/BOM.pdf", client) == ("bom", "rule")
    assert client.calls == []


def test_model_decides_when_rules_defer():
    client = MockLLMClient(response={"doc_class": "quotation"})
    result = classify_document("/p/vendors/ADPOWER/ADP-13158-2024-935.pdf", client,
                               text_head="Commercial offer, total price USD 1,200,000")
    assert result == ("quotation", "llm")
    assert len(client.calls) == 1


def test_filename_and_text_head_both_reach_the_model():
    client = MockLLMClient(response={"doc_class": "other"})
    classify_document("/p/vendors/M/HSD 230.pdf", client, text_head="Heat rate curve")
    sent = client.calls[0]["context_text"]
    assert "HSD 230.pdf" in sent
    assert "Heat rate curve" in sent


def test_text_head_is_truncated():
    client = MockLLMClient(response={"doc_class": "other"})
    classify_document("/p/vendors/M/HSD 230.pdf", client, text_head="x" * 5000)
    assert len(client.calls[0]["context_text"]) < 1500


def test_unknown_model_answer_degrades_to_other():
    client = MockLLMClient(response={"doc_class": "invoice"})
    assert classify_document("/p/vendors/M/HSD 230.pdf", client) == ("other", "llm")


def test_model_failure_degrades_to_other_without_raising():
    class Boom:
        supports_vision = True

        def __init__(self):
            self.calls = []

        def classify_structure(self, *a, **k):
            raise RuntimeError("provider down")

    assert classify_document("/p/vendors/M/HSD 230.pdf", Boom()) == ("other", "llm-failed")


def test_prompt_version_is_exposed():
    assert CLASSIFY_PROMPT_VERSION == "doc_class_v1"
