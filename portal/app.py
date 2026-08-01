# portal/app.py
import os
import zipfile
import streamlit as st
from dotenv import load_dotenv
from procurement import project as proj
from procurement.pipeline import run_ingestion, load_dataset
from procurement.pdf_llm import transcribe_pdf
from procurement.quote_select import pick_quote
from shared.llm.factory import get_client
from portal.views import comparison as comparison_view
from portal.views import compliance as compliance_view
from portal.views import statement as statement_view

load_dotenv()  # load a local .env if present (see .env.example)

ROOT = os.environ.get("PROCUREMENT_PROJECTS_ROOT", "projects")
os.makedirs(ROOT, exist_ok=True)

# The env var each provider needs for its API key (None = no key needed).
PROVIDER_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "bedrock": None,
    "mock": None,
}

st.set_page_config(page_title="Procurement Comparison Portal", layout="wide")
st.title("Procurement Comparison Portal")

# --- Sidebar: project selection / creation ---
st.sidebar.header("Projects")
projects = proj.list_projects(ROOT)
names = [p.slug for p in projects]
# A just-created project is applied here, before the widget is instantiated —
# Streamlit forbids assigning to a widget's key once it exists.
if "_pending_project" in st.session_state:
    st.session_state["project_choice"] = st.session_state.pop("_pending_project")

choice = st.sidebar.selectbox("Open project", ["<new>"] + names, key="project_choice")

# --- Sidebar: LLM provider (kept after the project selectbox so the project
# selector stays the first sidebar selectbox; rendered before any st.stop()). ---
st.sidebar.header("LLM provider")
_provider_options = ["anthropic", "openai", "mock"]
_default_provider = os.environ.get("LLM_PROVIDER", "anthropic")
if _default_provider not in _provider_options:
    _provider_options.append(_default_provider)
provider = st.sidebar.selectbox(
    "Extraction provider", _provider_options,
    index=_provider_options.index(_default_provider),
    help="Which LLM API runs the bid extraction. Set the matching API key in your .env.",
)
os.environ["LLM_PROVIDER"] = provider

_needed_key = PROVIDER_KEYS.get(provider)
if _needed_key and not os.getenv(_needed_key):
    st.warning(
        f"{_needed_key} is not set — extraction with '{provider}' will fail. "
        f"Add it to your .env (copy .env.example) and restart."
    )

if choice == "<new>":
    new_name = st.sidebar.text_input("New project name")
    target = st.sidebar.text_input("Target currency", value="USD")
    if st.sidebar.button("Create"):
        if new_name.strip():
            created = proj.create_project(ROOT, new_name.strip(), target_currency=target.strip() or "USD")
            # Open the project we just made, otherwise the rerun lands back on
            # this screen and a successful create is indistinguishable from a no-op.
            st.session_state["_pending_project"] = created.slug
            st.rerun()
        else:
            st.sidebar.error("Enter a project name first.")
    st.info("Create a project in the sidebar to begin.")
    st.stop()

project = proj.load_project(ROOT, choice)
st.subheader(f"Project: {project.name}")

# --- Requirements doc ---
st.markdown("### 1. Requirements document")
req = st.file_uploader("Upload the requirements document", type=["pdf", "docx", "xlsx"], key="req")
if req is not None:
    dest = os.path.join(ROOT, project.slug, "requirements", os.path.basename(req.name))
    with open(dest, "wb") as fh:
        fh.write(req.getbuffer())
    project.requirements_file = req.name
    proj.save_project(ROOT, project)
    st.success(f"Stored requirements: {req.name}")
elif project.requirements_file:
    st.caption(f"Attached: {project.requirements_file}")

# --- Vendor folders (ZIP) ---
st.markdown("### 2. Vendor folders (ZIP — top-level subfolders are vendors)")
vzip = st.file_uploader("Upload vendor ZIP", type=["zip"], key="vzip")
if vzip is not None:
    tmp = os.path.join(ROOT, project.slug, "_upload.zip")
    with open(tmp, "wb") as fh:
        fh.write(vzip.getbuffer())
    try:
        vendors = proj.unpack_vendor_zip(ROOT, project.slug, tmp)
        st.success(f"Detected vendors: {', '.join(vendors)}")
    except (ValueError, zipfile.BadZipFile) as e:
        st.error(f"Rejected archive: {e}")
    finally:
        os.remove(tmp)
    project = proj.load_project(ROOT, project.slug)

if project.vendors:
    for v in project.vendors:
        files = proj.vendor_files(ROOT, project.slug, v)
        st.caption(f"**{v}** — {len(files)} files · quote: {os.path.basename(pick_quote(files) or '—')}")

# --- FX rates ---
st.markdown("### 3. FX rates (to target currency)")
st.caption(f"Target currency: {project.target_currency}. Enter a rate for any other currency the vendors used.")
fx_input = st.text_input("FX rates as CUR=rate, comma-separated (e.g. EUR=1.08)",
                         value=",".join(f"{k}={v}" for k, v in project.fx_rates.items()))
if st.button("Save FX rates"):
    rates = {}
    for pair in fx_input.split(","):
        if "=" in pair:
            k, val = pair.split("=", 1)
            try:
                rates[k.strip().upper()] = float(val)
            except ValueError:
                pass
    project.fx_rates = rates
    proj.save_project(ROOT, project)
    st.success(f"Saved FX rates: {rates}")

# --- Run ---
st.markdown("### 4. Run ingestion")
if st.button("Run ingestion", type="primary", disabled=not project.vendors):
    with st.spinner("Extracting and comparing vendor bids..."):
        # The scanned-PDF transcription fallback uses Anthropic; only enable it
        # when an Anthropic key is available (works even if extraction runs on OpenAI).
        _fallback = transcribe_pdf if os.getenv("ANTHROPIC_API_KEY") else None
        run_ingestion(ROOT, project.slug, get_client(), pdf_fallback=_fallback)
    st.success("Done.")

# --- Results ---
# Two screens, not the phase-4 shell split: the statement stays the landing
# view (first tab) and compliance hangs beside it.
st.markdown("### 5. Results")

# Rendered before the statement: when this roster is present the pre-reviewed
# comparison is the answer the reviewer came for, and it needs no ingestion run.
# It sits above the tabs rather than inside one for both halves of that reason —
# it stays the first thing seen whichever tab is selected, and it renders nothing
# (`applies_to` returns early) for any other roster, so it costs those projects
# no space. It is deliberately not filed under Compliance: that tab is derived
# from requirements.json plus facts.json, and this is a hand-reviewed workbook.
comparison_view.render(project.vendors)

_statement_tab, _compliance_tab = st.tabs(["Comparative Statement", "Compliance"])

with _statement_tab:
    statement_view.render(ROOT, project.slug)

    dataset = load_dataset(ROOT, project.slug)
    if dataset:
        st.markdown("#### Per-vendor extracted bids")
        for bid in dataset["bids"]:
            with st.expander(f"{bid['vendor']} — {bid['extraction_status']}"):
                st.json(bid)
        st.markdown("#### Normalization adjustments")
        for n in dataset["normalized"]:
            if n["adjustments"]:
                st.write(f"**{n['vendor']}** → {n['normalized_total']} {n['normalized_currency']}")
                st.table(n["adjustments"])

with _compliance_tab:
    compliance_view.render(ROOT, project.slug)
