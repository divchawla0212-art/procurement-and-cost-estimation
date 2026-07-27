# portal/app.py
import os
import zipfile
import streamlit as st
from procurement import project as proj
from procurement.pipeline import run_ingestion, load_dataset
from procurement.pdf_llm import transcribe_pdf
from procurement.export import comparison_to_rows, comparison_to_xlsx_bytes, comparison_to_csv_str
from procurement.quote_select import pick_quote
from procurement.models import ComparisonTable
from shared.llm.factory import get_client

ROOT = os.environ.get("PROCUREMENT_PROJECTS_ROOT", "projects")
# Default to real Anthropic extraction when a provider isn't explicitly chosen,
# so a set ANTHROPIC_API_KEY is actually used (get_client() otherwise defaults to mock).
os.environ.setdefault("LLM_PROVIDER", "anthropic")

st.set_page_config(page_title="Procurement Comparison Portal", layout="wide")
st.title("Procurement Comparison Portal")

if not os.getenv("ANTHROPIC_API_KEY"):
    st.warning("ANTHROPIC_API_KEY is not set — extraction will fail. Set it and restart to run real extraction.")

os.makedirs(ROOT, exist_ok=True)

# --- Sidebar: project selection / creation ---
st.sidebar.header("Projects")
projects = proj.list_projects(ROOT)
names = [p.slug for p in projects]
choice = st.sidebar.selectbox("Open project", ["<new>"] + names)

if choice == "<new>":
    new_name = st.sidebar.text_input("New project name")
    target = st.sidebar.text_input("Target currency", value="USD")
    if st.sidebar.button("Create") and new_name.strip():
        proj.create_project(ROOT, new_name.strip(), target_currency=target.strip() or "USD")
        st.rerun()
    st.info("Create a project in the sidebar to begin.")
    st.stop()

project = proj.load_project(ROOT, choice)
st.subheader(f"Project: {project.name}")

# --- Requirements doc ---
st.markdown("### 1. Requirements document")
req = st.file_uploader("Upload the requirements document", type=["pdf", "docx", "xlsx"], key="req")
if req is not None:
    dest = os.path.join(ROOT, project.slug, "requirements", req.name)
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
        run_ingestion(ROOT, project.slug, get_client(), pdf_fallback=transcribe_pdf)
    st.success("Done.")

# --- Results ---
dataset = load_dataset(ROOT, project.slug)
if dataset:
    st.markdown("### 5. Results")
    table = ComparisonTable.model_validate(dataset["comparison"])
    st.dataframe(comparison_to_rows(table), use_container_width=True)

    c1, c2 = st.columns(2)
    c1.download_button("Download Excel", comparison_to_xlsx_bytes(table),
                       file_name=f"{project.slug}-comparison.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    c2.download_button("Download CSV", comparison_to_csv_str(table),
                       file_name=f"{project.slug}-comparison.csv",
                       mime="text/csv")

    st.markdown("#### Per-vendor extracted bids")
    for bid in dataset["bids"]:
        with st.expander(f"{bid['vendor']} — {bid['extraction_status']}"):
            st.json(bid)
    st.markdown("#### Normalization adjustments")
    for n in dataset["normalized"]:
        if n["adjustments"]:
            st.write(f"**{n['vendor']}** → {n['normalized_total']} {n['normalized_currency']}")
            st.table(n["adjustments"])
