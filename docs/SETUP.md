# Setup Guide — Fresh Machine

From a computer with **nothing installed** to a running portal, API, and web UI.

Every code block below is **one command** — copy it, paste it, run it, move to the next.

The main track is **Windows 11 / PowerShell** (open *Terminal* from the Start menu).
macOS and Linux equivalents are at the bottom.

What you end up with:

| Piece | What it is | URL |
|---|---|---|
| Streamlit portal | upload requirements + vendor ZIPs, run ingestion | http://localhost:8501 |
| FastAPI backend | read-only compliance data over the store | http://localhost:8000 |
| React web UI | enterprise comparison matrix | http://localhost:5173 |

---

## 1. Install the prerequisites

Windows 11 ships with `winget`, so you do not need to download any installers by hand.

**Python 3.12** (the version the project targets):

```powershell
winget install --id Python.Python.3.12 -e --source winget
```

**Node.js LTS** (needed for the React UI — Vite 8 requires Node 20.19+):

```powershell
winget install --id OpenJS.NodeJS.LTS -e --source winget
```

**Git**:

```powershell
winget install --id Git.Git -e --source winget
```

Now **close the terminal and open a new one** so the new `PATH` takes effect.

Confirm all three are visible:

```powershell
python --version; node --version; git --version
```

You want Python 3.12.x, Node v20.19+ (v24 is fine), and any Git 2.x.

---

## 2. Get the code

Pick a folder to work in:

```powershell
mkdir -Force $HOME\git_workspace | Set-Location
```

Clone over HTTPS (no SSH key needed — a browser sign-in prompt handles auth):

```powershell
git clone https://github.com/RahulJana/procurement-and-cost-estimation.git
```

Move into the repo. **Every remaining command in this guide runs from here.**

```powershell
Set-Location $HOME\git_workspace\procurement-and-cost-estimation
```

---

## 3. Create the Python environment

Create the virtual environment in the repo root:

```powershell
python -m venv .venv
```

Upgrade pip inside it:

```powershell
.venv\Scripts\python.exe -m pip install --upgrade pip
```

Install the project in editable mode with dev extras (test suite included):

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

This pulls in `pydantic`, `openpyxl`, `pyyaml`, `anthropic`, `openai`, `pypdf`,
`python-docx`, `streamlit`, `fastapi`, `uvicorn` — and puts the local
`procurement`, `shared`, `cost_estimation`, `portal`, and `api` packages on the
import path. Editable means source edits take effect with no reinstall.

> The guide calls `.venv\Scripts\python.exe` explicitly everywhere, so you never
> have to remember whether the venv is activated. If you prefer activating it,
> see [Optional: activating the venv](#optional-activating-the-venv) below.

---

## 4. Install the web UI dependencies

```powershell
npm --prefix web install
```

---

## 5. Verify before configuring anything

The test suite is key-free by design — it runs against `shared/llm/mock_client.py`.
Run it **now**, while there is still no `.env`:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Expected on a fresh clone: **647 passed, 6 skipped, 0 failed.**

Those 6 skips are correct, not a problem: 3 need provider credentials you have not
set yet, and 3 need an untracked `data/` sample directory a fresh clone does not have.

> After you create `.env` in the next step, one test —
> `tests/test_portal_app.py::test_missing_api_key_does_not_block_creation` — starts
> failing on your machine and **that is expected**. `portal/app.py` calls
> `load_dotenv()`, which puts `ANTHROPIC_API_KEY` back after the test removes it.
> See the *Running things* section of [`CLAUDE.md`](../CLAUDE.md) for both green
> baselines. Do not "fix" it.

---

## 6. Configure your API key

Copy the template:

```powershell
Copy-Item .env.example .env
```

Open it in Notepad and fill in `ANTHROPIC_API_KEY=`:

```powershell
notepad .env
```

Minimum to run real ingestion — leave `LLM_PROVIDER=anthropic` and set:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Save and close. `.env` is git-ignored; never commit a real key.

Other switches in that file, all optional: `LLM_PROVIDER` (`anthropic` | `openai` |
`bedrock` | `gemini` | `mock`), `OPENAI_API_KEY`, `LLM_MODEL`, `PDF_LLM_MODEL`,
and `PROCUREMENT_PROJECTS_ROOT` (where projects are written — defaults to `projects/`).

Want to click around before you have a key? Set `LLM_PROVIDER=mock` instead.

`LLM_PROVIDER` sets the default. You can also override it per run: step 4 of the
React app's Setup wizard has an **Extraction provider** dropdown, and whatever you
pick there applies to that one ingestion run only — reload the page and it goes
back to the default above. Providers you have not configured are listed but
disabled, each showing the variable it still needs. Keys are never entered in the
browser; they only ever come from this `.env`.

---

## 7. Run the Streamlit portal (upload + ingestion)

This is where data comes *in*.

```powershell
.venv\Scripts\python.exe -m streamlit run portal/app.py
```

Open http://localhost:8501 — create a project, upload the requirements file and the
vendor ZIP, set FX rates, then click **Run ingestion**.

Stop it with `Ctrl+C` when you are done.

---

## 8. Run the review UI (API + React)

The comparison matrix is read-only and needs ingested data under `projects/` first.
It is two processes, so you need **two terminals**, both in the repo root.

**Terminal 1 — the API:**

```powershell
.venv\Scripts\python.exe -m uvicorn api.main:app --reload --port 8000
```

**Terminal 2 — the React app:**

```powershell
npm --prefix web run dev
```

Open http://localhost:5173 and pick a project in the sidebar. Vite proxies `/api`
straight to `127.0.0.1:8000`, so both must be running. This UI never writes to the store.

---

## 9. You're set — quick reference

| Task | Command |
|---|---|
| Run tests | `.venv\Scripts\python.exe -m pytest` |
| Streamlit portal | `.venv\Scripts\python.exe -m streamlit run portal/app.py` |
| API | `.venv\Scripts\python.exe -m uvicorn api.main:app --reload --port 8000` |
| Web UI | `npm --prefix web run dev` |
| Lint the web app | `npm --prefix web run lint` |
| Build the web app | `npm --prefix web run build` |
| Costing CLI | `.venv\Scripts\cost-est.exe --help` |

Inside Claude Code, prefer the preview tooling over bare commands — `.claude/launch.json`
already defines `procurement-portal`, `procurement-api`, and `enterprise-web`.

---

## Optional: activating the venv

If you would rather type `python` / `pytest` / `streamlit` without the
`.venv\Scripts\` prefix, activate the environment once per terminal:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks that with an execution-policy error, allow signed local
scripts for your user account (one time, then re-run the activate command):

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

---

## Troubleshooting

**`python` opens the Microsoft Store instead of running.**
Windows' App Execution Aliases are shadowing the real install. Turn off the Python
aliases under *Settings → Apps → Advanced app settings → App execution aliases*, or
just use the versioned launcher:

```powershell
py -3.12 -m venv .venv
```

**`winget` is not recognized.**
Update *App Installer* from the Microsoft Store, then reopen the terminal.

**`npm` is not recognized right after installing Node.**
The `PATH` change only applies to new terminals. Close this one and open a new one.

**Port already in use (8501 / 8000 / 5173).**
Find what is holding it — replace `8000` with the port in question:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object OwningProcess
```

**The web UI loads but every panel is empty.**
Either the API is not running on 8000, or nothing has been ingested yet. The matrix
reads from `projects/<slug>/store/` — run ingestion in the Streamlit portal first.

**`pip install -e ".[dev]"` fails compiling a dependency.**
You are almost certainly not on 3.12. Check with `.venv\Scripts\python.exe --version`,
delete `.venv`, and recreate it with `py -3.12 -m venv .venv`.

---

## macOS / Linux

Same sequence, different commands. Install [Homebrew](https://brew.sh) first on macOS
(on Debian/Ubuntu use `sudo apt install python3.12 python3.12-venv nodejs npm git`).

```bash
brew install python@3.12 node git
```

```bash
git clone https://github.com/RahulJana/procurement-and-cost-estimation.git
```

```bash
cd procurement-and-cost-estimation
```

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/python -m pip install -e ".[dev]"
```

```bash
npm --prefix web install
```

```bash
.venv/bin/python -m pytest -q
```

```bash
cp .env.example .env
```

```bash
.venv/bin/python -m streamlit run portal/app.py
```

```bash
.venv/bin/python -m uvicorn api.main:app --reload --port 8000
```

```bash
npm --prefix web run dev
```
