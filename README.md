# ShangHeng SI (商衡SI)

**English** | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

An AI-assisted first-pass reviewer for business documents (contracts, quotations, orders, inquiries and letters), designed to run inside a company LAN.

**Current release: V3.3.0** (released 2026-11-15).

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

The sidebar has five entries:

| Module | What it does |
|-|-|
| Workbench | The unified home (called "Assistant" before V3.2): start a review, ask questions about a document, and discuss issues in one session |
| Smart Review | The document review flow described above |
| Meeting Minutes | Transcribe meeting audio locally and generate minutes |
| Translation | Translate documents and pasted text; runs locally |
| History | All past review tasks and sessions (called "Review history" before V3.2) |

Highlights of V3.3.0:

- **Unified "page not found" view**: an unknown path no longer silently redirects home; it opens a "page not found" page inside the main frame, with a way back to the workbench.
- **Three waiting indicators**: block loading uses a brand three-colour review band; file parsing shows a small sheet being scanned line by line and redacted as the scan passes; chat waits use gathering ink dots. All three appear only after a unified 300 ms delay, so fast operations never flash an indicator.
- **Time-aware home greeting**: the home page greets the user by display name and time of day (Beijing time).
- **Missing-choice guidance**: the submit button stays clickable when a required choice is missing; unchosen fields are marked with a dashed outline, and clicking scrolls to the first missing one.
- **Adopt / ignore moves to the next finding**: after a finding is adopted or ignored it collapses and the next pending finding opens, the same behaviour as the A / I shortcut keys.
- **Steadier citation anchors**: knowledge-base and history reference numbers stay stable within a session, and common citation variants are recognised and anchored. This was verified with a real model (19 calls, targets met). Meeting data created on V3.1.0 was likewise verified to upgrade cleanly, with no code change.
- **Technical terms and model numbers**: industrial-control terms are no longer flagged by mistake, product model numbers are kept whole instead of being split, and technical-term handling is generalised across trades.
- **Outbound-extra leak list**: leaks detected in outbound extras are listed by section, and confirmation stays locked until all of them are resolved.
- **Incremental upgrade packages**: the release script finds a working Python 3.11+ on its own. An upgrade package can carry only the images whose source changed (the V3.3.0 incremental package is about 1.8 GB, vs about 9.4 GB for the full package), and a fresh install rejects incremental packages.

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

V3.3.0 is the current release (2026-11-15) and is deployed on the production server. The release report is [docs/v33/reports/V33-Z.md](docs/v33/reports/V33-Z.md) and the production upgrade record is [docs/acceptance/V3.3.0升级记录.md](docs/acceptance/V3.3.0升级记录.md). See [docs/TASKS.md](docs/TASKS.md) for the task list.

## Documentation

- [docs/SPEC.md](docs/SPEC.md): full product and development specification (Chinese)
- [docs/TASKS.md](docs/TASKS.md): task breakdown and progress
- [docs/open_questions.md](docs/open_questions.md): decisions on details not covered by the spec
- `docs/acceptance/`: installation and upgrade records for the production server; `docs/ops/`: operations guide (install, upgrade, backup, restore)
- [CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md): rules for AI coding assistants working on this repository
