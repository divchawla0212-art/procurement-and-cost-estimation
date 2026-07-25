from pydantic import BaseModel, ConfigDict


class ProvenanceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_path: str
    sheet: str | None = None
    cell: str | None = None
    extractor: str
    prompt_version: str | None = None
