import os
import pytest
from pydantic import BaseModel
from shared.llm.interface import LLMClient
from shared.llm.bedrock_client import BedrockClient


class _Layout(BaseModel):
    header_row: int
    columns: dict[str, int]


def test_bedrock_client_satisfies_protocol():
    client = BedrockClient(region="us-east-1")
    assert isinstance(client, LLMClient)
    assert client.supports_vision is True


def test_default_model_is_bedrock_prefixed():
    # Bedrock model IDs carry an "anthropic." provider prefix.
    assert BedrockClient(region="us-east-1").model.startswith("anthropic.")


def test_build_tool_uses_schema():
    tool = BedrockClient(region="us-east-1")._build_tool(_Layout)
    assert tool["name"] == "emit_structure"
    assert "header_row" in tool["input_schema"]["properties"]


def test_construction_needs_no_aws_sdk_or_creds():
    # Importing the module and constructing the client must not require boto3,
    # AWS credentials, or a network call — the Bedrock client is built lazily
    # inside classify_structure. Region falls back to the AWS_REGION env var.
    client = BedrockClient(model="anthropic.claude-sonnet-5", region="us-west-2")
    assert client.model == "anthropic.claude-sonnet-5"
    assert client.region == "us-west-2"


@pytest.mark.skipif(not os.getenv("AWS_REGION"), reason="no AWS_REGION / Bedrock credentials")
def test_live_classify_structure_returns_dict():
    client = BedrockClient()
    out = client.classify_structure(
        prompt="Return header_row=1 and empty columns.",
        output_schema=_Layout,
        context_text="col A | col B",
    )
    assert isinstance(out, dict)
    assert "header_row" in out
