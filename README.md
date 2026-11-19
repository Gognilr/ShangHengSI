# ShangHeng SI (商衡SI)

**English** | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

An AI-assisted first-pass reviewer for business documents (contracts, quotations, orders, inquiries and letters), designed to run inside a company LAN.

**Current release: V3.4.0** (released 2026-11-19).

Sensitive information never leaves the internal network: documents are parsed, checked and **redacted locally**. Only the redacted text is sent to a cloud LLM, and the results are restored to real names locally before they are shown.

> ⚠️ This tool performs a **preliminary** review only. Its output does not replace review by legal, compliance or business professionals.

## How it works

```text
Upload → Local parsing & checks → Automatic redaction → User confirmation
       → Cloud LLM review → Local restoration & issue list → Follow-up questions & discussion
       → Word report / annotated copy / revised copy
```

1. **Upload**: one or more files, with the stance (sales / purchase / other), source (sent by us / received) and outbound policy.
2. **Local processing**: parsing into a unified document model, local consistency checks (amounts, totals, dates, currencies), and redaction of company names, people, amounts, accounts, phone numbers and more, replaced by codes such as `客户_A` (Customer A) or `[金额_1]` (Amount 1).
3. **Confirmation**: the user confirms which party is "us", reviews the redaction summary and previews the exact text that will be sent.
4. **AI review**: the LLM gives a verdict on every review requirement and lists issues with locations and verbatim quotes. Quotes are verified locally.
5. **Results**: issues are shown with real names restored, streamed in as the review progresses. Users can ask follow-up questions (also redacted before sending), discuss individual issues and adopt a discussion draft, and export a Word report, an annotated copy or a revised copy. Review replies carry citation anchors; clicking one shows the cited source (knowledge base, history or law) in a popover.

Tasks with the policy `local_only` never call the model. Only local checks are performed.

## Modules and highlights

The sidebar is grouped by task (V3.4: one entry per job):

| Module | What it does |
|-|-|
| Workbench | The unified home: ask questions about documents and discuss issues in one session; four entry tiles with live status |
| Document Review | The document review flow described above (start page `/review`) |
| Bid Pre-screening | For the tendering side: compare 3–5 bids against the tender for qualification, compliance and collusion signals (new in V3.4) |
| Meeting Minutes | Transcribe meeting audio locally and generate minutes |
| Translation | Translate documents and pasted text; runs locally |
| History | All past review tasks and sessions (called "Review history" before V3.2) |

Highlights of V3.4.0:

- **Bid pre-screening (new menu)**: upload the tender and each bidder's files; local checks find shared contacts, identical file metadata and images, copied passages, price patterns, over-ceiling and inconsistent totals, and expired certificates. Only redacted snippets go to the model to check each requirement. The first tab is a conclusion in three layers (certain / to verify / reference) with collusion cases built as evidence chains, and a Word "pre-screening findings" report for the evaluation committee. No scoring, ranking or recommendation.
- **One entry per job**: the sidebar is grouped into Workbench, document checks (Document Review, Bid Pre-screening), tools (Meeting Minutes, Translation) and History; highlighting no longer jumps. Settings moved into the account menu.
- **"Plain paper" look and new logo**: one continuous page without grey cards, glows or gradients; pine-needle green as the main colour with a pine-cone ochre accent; the new "pine scale" logo.
- **Document cross-check runs locally**: the shipping-document check compares quantities, weights, amounts, dates and reference numbers on this machine; the model only helps locate fields it could not find.
- **Better scanned documents**: OCR upgraded to RapidOCR 3.10 with PP-OCRv6 small models (bundled, no download); ruled tables on scanned pages become table cells; unruled quotation tables, tables continued across pages, and repeated PDF headers/footers are handled.
- **History**: seven columns without horizontal scrolling, type filter (all / document review / bid pre-screening / chat), search as you type.

## Security and privacy by design

- **Single egress point**: only `backend/app/llm/gateway.py` may access the internet. The gateway always runs, in order: monthly call limit check → outbound re-scan → send → log to `llm_calls`.
- **Outbound re-scan**: before sending, the text is scanned again for any real value from the redaction map and for unredacted phone numbers, emails, accounts, IDs and amounts. Anything found blocks the call.
- **Text only**: images and original files are never sent to the model.
- **Encryption at rest**: original files, redaction maps and sensitive fields are encrypted with AES-256-GCM using a master key.
- **Strict ownership**: every task endpoint checks the owner. Other users, including administrators, get a 404.
- **Clean logs**: logs never contain document text, real names, amounts or API keys.

## Supported documents

| Type | Examples |
|-|-|
| Contract | Sales contracts, purchase contracts, framework agreements |
| Quotation | Quotations, proforma invoices |
| Order | Purchase orders, supply orders |
| Inquiry | Inquiry letters, RFQs |
| Letter | Supply letters, commitment letters, notices, claims |

Supported formats are DOCX, PDF (text and scanned), XLSX, JPG and PNG. Legacy Office formats are converted locally by the offline converter service (see Architecture).

Review dimensions: wording, logic, legal, compliance & sanctions, and commercial terms.

## Tech stack

| Layer | Choice |
|-|-|
| Frontend | Vue 3, TypeScript, Vite, Element Plus, Pinia, Vue Router |
| Backend | Python 3.12, FastAPI, Uvicorn, Pydantic v2 |
| Database & queue | PostgreSQL 16 (also the job queue, via `SELECT … FOR UPDATE SKIP LOCKED`) |
| ORM & migrations | SQLAlchemy 2, Alembic, psycopg 3 |
| Parsing | python-docx + lxml, openpyxl, pdfplumber, RapidOCR (onnxruntime, CPU) |
| Crypto | cryptography (AES-256-GCM), argon2-cffi (argon2id) |
| LLM | Any OpenAI-compatible Chat Completions API (e.g. DeepSeek), called through `httpx` in the gateway only |
| Deployment | Docker Compose v2, Caddy 2 (HTTPS + static files) |

## Architecture

Seven services on a single Ubuntu Server 24.04 VM, defined in `deploy/docker-compose.yml`:

```text
Browser (LAN, HTTPS)
  └─> web     Caddy: frontend static files + /api proxy
        └─> app     FastAPI: all APIs, follow-up chat
              ├─> db      PostgreSQL: data + job queue
              └─> /data/files (encrypted originals, shared volume)
worker    parsing, OCR, local checks, redaction, AI review, backup, cleanup
          └─> cloud LLM API (the only allowed outbound address)
converter / transcriber / translator
          offline helpers with no network access at all (network_mode: none);
          they exchange files with the worker only through a shared /spool volume
          (legacy-format conversion, audio transcription, local translation)
```

## Repository layout

```text
backend/    FastAPI app, worker, Alembic migrations, resources, tests
frontend/   Vue 3 single-page app
converter/  offline legacy-format conversion service (no network)
transcriber/ offline audio transcription service (no network)
translator/ offline local translation service (no network)
deploy/     docker-compose.yml, Caddyfile, .env.example
docs/       SPEC.md (full specification), TASKS.md (task list), open_questions.md, design/,
            per-version folders (v2/ … v33/), acceptance/ (install & upgrade records), ops/
```

## Getting started (development)

Requirements: Docker with Compose v2, and Node.js for frontend development. All backend commands run in Docker. The commands below are for PowerShell; in bash, replace `${PWD}` with `$(pwd)`.

```powershell
# Build the runtime image and generate a development master key (it is never overwritten)
docker build -t aireview-backend:dev --target runtime backend
docker run --rm -v "${PWD}/deploy/secrets:/out" aireview-backend:dev python -m app.cli gen-key --out /out/master_key

# Prepare the environment file, then set POSTGRES_PASSWORD
# (change HTTP_PORT/HTTPS_PORT to 8080/8443 if 80/443 are in use)
copy deploy\.env.example deploy\.env

# Start the stack (the app runs `alembic upgrade head` on startup)
docker compose -f deploy/docker-compose.yml up -d --build
```

Frontend development server (proxies `/api` to the running stack):

```powershell
npm --prefix frontend ci --include=dev
npm --prefix frontend run dev        # http://localhost:5173
```

Run the backend tests:

```powershell
docker build -t aireview-backend-test --target test backend
docker network create aireview-test
docker run -d --name aireview-test-db --network aireview-test -e POSTGRES_PASSWORD=test postgres:16.15
docker run --rm --network aireview-test -e TEST_DATABASE_URL=postgresql://postgres:test@aireview-test-db:5432/postgres -v "${PWD}/backend:/srv/backend" aireview-backend-test pytest -q
```

`deploy/.env`, `deploy/secrets/` and `deploy/backup/` are not tracked in git.

## Project status

V3.4.0 is the current release (2026-11-19) and is deployed on the production server. The release report is [docs/v34/reports/V34-Z.md](docs/v34/reports/V34-Z.md) and the production upgrade record is [docs/acceptance/V3.4.0升级记录.md](docs/acceptance/V3.4.0升级记录.md). See [docs/TASKS.md](docs/TASKS.md) for the task list.

## Documentation

- [docs/SPEC.md](docs/SPEC.md): full product and development specification (Chinese)
- [docs/TASKS.md](docs/TASKS.md): task breakdown and progress
- [docs/open_questions.md](docs/open_questions.md): decisions on details not covered by the spec
- `docs/acceptance/`: installation and upgrade records for the production server; `docs/ops/`: operations guide (install, upgrade, backup, restore)
- [CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md): rules for AI coding assistants working on this repository

## License

This project's source code is released under the **Functional Source License 1.1 (FSL-1.1-ALv2)**; see [LICENSE](./LICENSE):

- You may freely view, use and modify this source code for **non-competing purposes**, including deploying it inside your own organization;
- Two years after each version is released, that version automatically converts to the Apache License 2.0;
- Without the licensor's prior written authorization, you must not use this software to provide third parties with a product or service having the same or substantially similar functionality as this software; for competing commercial use, please apply for a commercial license as described in [COMMERCIAL_LICENSE.md](./COMMERCIAL_LICENSE.md);
- The "商衡SI / ShangHengSI" name and logos are not covered by the source code license; see [TRADEMARK.md](./TRADEMARK.md).
