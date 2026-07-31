# How to Run the Procurement Comparison Portal

## 1. Set up the Python environment

Requires Python 3.12+. Create a virtual environment in the repo root and install the project into it:

```bash
python -m venv .venv
```

Activate it:

| Shell | Command |
| --- | --- |
| PowerShell | `.\.venv\Scripts\Activate.ps1` |
| Git Bash / WSL | `source .venv/Scripts/activate` |
| macOS / Linux | `source .venv/bin/activate` |

Then install the project in editable mode, with dev extras for the test suite:

```bash
pip install -e ".[dev]"
```

This installs all runtime dependencies (`pydantic`, `openpyxl`, `pyyaml`, `anthropic`, `pypdf`, `streamlit`) **and** puts the local `procurement`, `shared`, `cost_estimation`, and `portal` packages on the import path. The editable link points back at your source tree, so code edits take effect immediately with no reinstall.

Verify the environment:

```bash
pytest -q
```

You should see all tests passing (a few are skip-guarded and will report as skipped).

## 2. Set your Anthropic API key

Real bid extraction calls the Anthropic API. Without this, the portal loads but shows a warning and extraction fails.

```bash
export ANTHROPIC_API_KEY=sk-...
```

(PowerShell: `$env:ANTHROPIC_API_KEY = "sk-..."`)

## 3. (Optional) Choose where projects are stored

Defaults to `./projects` in the repo root. Override with:

```bash
export PROCUREMENT_PROJECTS_ROOT=/path/to/projects
```

## 4. Start the portal

With the virtual environment activated, run from the **repo root**:

```bash
streamlit run portal/app.py
```

The app opens at [http://localhost:8501](http://localhost:8501).

> If you see `ModuleNotFoundError: No module named 'procurement'`, the editable install from step 1 hasn't been done in the environment you're using. Streamlit puts only the script's own folder (`portal/`) on `sys.path`, not the repo root, so the local packages are found via the editable install rather than the current directory. Re-run `pip install -e ".[dev]"`, or as a one-off workaround use `python -m streamlit run portal/app.py`.

## 5. Use the portal

1. In the sidebar, select `<new>`, enter a project name and target currency, and click **Create**.
2. Zip your vendor quote folders into a single ZIP and upload it. The portal detects each vendor and their quote documents.
3. If any vendor quoted in a foreign currency, enter the FX rate (e.g. `EUR=1.08`) and save.
4. Click **Run ingestion** to extract and normalize bids.
5. Review the populated comparison table, per-vendor extracted bids, and normalization adjustments.
6. Download the results as Excel or CSV.

## Stopping the portal

Press `Ctrl+C` in the terminal running Streamlit.
