from shared.provenance import ProvenanceRef
from cost_estimation.models.schema import (
    DocType, Document, CostItem, RateBuildUp, WorkPackage, CostDataset,
)


def _prov():
    return ProvenanceRef(document_path="c.xlsx", sheet="S", cell="K10", extractor="xlsx")


def test_priced_cost_item_roundtrips():
    item = CostItem(
        code="E.03.03.01.01", description="Conduit", uom="m", quantity=6,
        rate_buildup=RateBuildUp(unit_price=10.0, provenance=_prov()),
        total=60.0, priced=True, provenance=_prov(),
    )
    dumped = item.model_dump()
    assert CostItem.model_validate(dumped).total == 60.0


def test_blank_item_has_no_buildup():
    item = CostItem(code="E.01", description="x", uom="m", quantity=6, provenance=_prov())
    assert item.priced is False
    assert item.rate_buildup is None


def test_dataset_composes():
    ds = CostDataset(
        documents=[Document(path="c.xlsx", doc_type=DocType.COSTING_WORKBOOK)],
        work_packages=[WorkPackage(name="TF Main Elec.", discipline="electrical", area="TF")],
    )
    assert ds.documents[0].doc_type == DocType.COSTING_WORKBOOK
    assert ds.work_packages[0].area == "TF"
