import os

from procurement.compliance import vocabulary_sha
from procurement.pipeline import run_ingestion
from procurement.project import create_project, load_project, save_project
from procurement.store import snapshots

from tests.test_pipeline_rfq import RfqClient      # the schema-aware stub


class VocabClient(RfqClient):
    """Records the context text handed to each datasheet call."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.tech_context = []

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        if "facts" in output_schema.model_fields:
            self.tech_context.append(context_text)
        return super().classify_structure(prompt, output_schema, context_text, images)


_MR = "ADN-AEC-ME-SPC-026 MR Gas Genset.txt"


def _project(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    rdir = tmp_path / "p" / "requirements"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / _MR).write_text("4.2.7 H2S at least 50 ppm", encoding="utf-8")
    vdir = tmp_path / "p" / "vendors" / "KERUI"
    vdir.mkdir(parents=True)
    (vdir / "Quotation.txt").write_text("base price 1000", encoding="utf-8")
    (vdir / "01 DataSheet Gas Generator.txt").write_text("H2S up to 70 ppm",
                                                         encoding="utf-8")
    project = load_project(root, "p")
    project.vendors = ["KERUI"]
    save_project(root, project)
    return root


def _datasheet(root):
    return next(d for d in snapshots.load_documents(root, "p")
                if d.path.endswith("01 DataSheet Gas Generator.txt"))


def test_the_datasheet_prompt_is_seeded_with_the_auto_parameter_names(tmp_path):
    root = _project(tmp_path)
    client = VocabClient()
    run_ingestion(root, "p", client)
    assert client.tech_context, "the datasheet was never extracted"
    assert "h2s_tolerance" in client.tech_context[0]


def test_the_requirements_pass_runs_before_the_datasheet_pass(tmp_path):
    root = _project(tmp_path)
    client = VocabClient()
    run_ingestion(root, "p", client)
    assert client.calls.index("requirements") < client.calls.index("facts")


def test_the_datasheet_records_the_vocabulary_it_was_extracted_under(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", VocabClient())
    assert _datasheet(root).vocabulary_sha == vocabulary_sha(["h2s_tolerance"])


def test_a_rerun_with_unchanged_requirements_makes_zero_calls(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", VocabClient())
    second = VocabClient()
    run_ingestion(root, "p", second)
    assert second.calls == []


def test_changing_a_requirement_reextracts_every_datasheet_exactly_once(tmp_path):
    # INV-8
    root = _project(tmp_path)
    run_ingestion(root, "p", VocabClient())
    (tmp_path / "p" / "requirements" / _MR).write_text(
        "4.2.7 continuous rating at least 500 kW", encoding="utf-8")

    second = VocabClient()
    second_response_parameter = "continuous_rating"
    second.requirement_parameter = second_response_parameter   # see RfqClient
    run_ingestion(root, "p", second)
    assert second.calls.count("facts") == 1
    assert _datasheet(root).vocabulary_sha == vocabulary_sha([second_response_parameter])

    third = VocabClient()
    third.requirement_parameter = second_response_parameter
    run_ingestion(root, "p", third)
    assert third.calls.count("facts") == 0, "the bump did not persist"


def test_a_cached_datasheet_keeps_its_vocabulary_sha_on_the_stored_record(tmp_path):
    # C2a, one collection over: a cache hit rebuilds the DocumentRecord from
    # the fresh inventory, so a fingerprint not copied across is silently
    # dropped. The run after the cache hit then re-extracts, forever. It takes
    # three runs to see, which is why no assertion above catches it.
    root = _project(tmp_path)
    run_ingestion(root, "p", VocabClient())
    expected = _datasheet(root).vocabulary_sha
    assert expected == vocabulary_sha(["h2s_tolerance"])

    run_ingestion(root, "p", VocabClient())                 # a cache hit
    assert _datasheet(root).vocabulary_sha == expected

    third = VocabClient()
    run_ingestion(root, "p", third)
    assert third.calls.count("facts") == 0


def test_the_vocabulary_does_not_invalidate_quotations_or_deviations(tmp_path):
    root = _project(tmp_path)
    (tmp_path / "p" / "vendors" / "KERUI"
     / "03 Attachment-2 Vendor Deviation Form.txt").write_text("4.2.7 differs",
                                                               encoding="utf-8")
    run_ingestion(root, "p", VocabClient())
    (tmp_path / "p" / "requirements" / _MR).write_text(
        "4.2.7 continuous rating at least 500 kW", encoding="utf-8")
    second = VocabClient()
    second.requirement_parameter = "continuous_rating"
    run_ingestion(root, "p", second)
    assert second.calls.count("facts") == 1
    assert second.calls.count("deviations") == 0
    assert second.calls.count("bid") == 0


def test_a_project_with_no_requirements_still_extracts_datasheets(tmp_path):
    root = _project(tmp_path)
    os.remove(os.path.join(root, "p", "requirements", _MR))
    client = VocabClient()
    run_ingestion(root, "p", client)
    assert client.calls.count("facts") == 1
    assert _datasheet(root).vocabulary_sha == vocabulary_sha([])
    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.technical, "an empty vocabulary must not suppress extraction"


def test_a_phase2_store_reextracts_each_datasheet_once_then_settles(tmp_path):
    # the one-time migration cost: stored vocabulary_sha is None
    root = _project(tmp_path)
    run_ingestion(root, "p", VocabClient())
    docs = snapshots.load_documents(root, "p")
    for doc in docs:
        if doc.path.endswith("01 DataSheet Gas Generator.txt"):
            doc.vocabulary_sha = None
    snapshots.save_documents(root, "p", docs)

    second = VocabClient()
    run_ingestion(root, "p", second)
    assert second.calls.count("facts") == 1
    third = VocabClient()
    run_ingestion(root, "p", third)
    assert third.calls.count("facts") == 0
