# ShangHeng SI (商衡SI)

**English** | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

An AI-assisted first-pass reviewer for business documents (contracts, quotations, orders, inquiries and letters), designed to run inside a company LAN.

Sensitive information never leaves the internal network: documents are parsed, checked and **redacted locally**. Only the redacted text is sent to a cloud LLM, and the results are restored to real names locally before they are shown.

> ⚠️ This tool performs a **preliminary** review only. Its output does not replace review by legal, compliance or business professionals.

## How it works

```text
Upload → Local parsing & checks → Automatic redaction → User confirmation
       → Cloud LLM review → Local restoration & issue list → Follow-up questions → Word report
```

1. **Upload**: one or more files, with the stance (sales / purchase / other), source (sent by us / received) and outbound policy.
2. **Local processing**: parsing into a unified document model, local consistency checks (amounts, totals, dates, currencies), and redaction of company names, people, amounts, accounts, phone numbers and more, replaced by codes such as `客户_A` (Customer A) or `[金额_1]` (Amount 1).
3. **Confirmation**: the user confirms which party is "us", reviews the redaction summary and previews the exact text that will be sent.
4. **AI review**: the LLM gives a verdict on every review requirement and lists issues with locations and verbatim quotes. Quotes are verified locally.
5. **Results**: issues are shown with real names restored. Users can ask follow-up questions (also redacted before sending) and export a Word report.

Tasks with the policy `local_only` never call the model. Only local checks are performed.

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

V1.0 target formats are DOCX, PDF (text and scanned), XLSX, JPG and PNG. DOCX is implemented first; PDF, OCR and XLSX follow in Milestone 3.

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

Four containers on a single Ubuntu Server 24.04 VM:

```text
Browser (LAN, HTTPS)
  └─> web     Caddy: frontend static files + /api proxy
        └─> app     FastAPI: all APIs, follow-up chat
              ├─> db      PostgreSQL: data + job queue
              └─> /data/files (encrypted originals, shared volume)
worker    parsing, OCR, local checks, redaction, AI review, backup, cleanup
          └─> cloud LLM API (the only allowed outbound address)
```

## Repository layout

```text
backend/    FastAPI app, worker, Alembic migrations, resources, tests
frontend/   Vue 3 single-page app
deploy/     docker-compose.yml, Caddyfile, .env.example
docs/       SPEC.md (full specification), TASKS.md (task list), open_questions.md, design/
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

V1.0 is under active development, following milestones M1–M8 in [docs/SPEC.md](docs/SPEC.md) §22. See [docs/TASKS.md](docs/TASKS.md) for current progress.

## Documentation

- [docs/SPEC.md](docs/SPEC.md): full product and development specification (Chinese)
- [docs/TASKS.md](docs/TASKS.md): task breakdown and progress
- [docs/open_questions.md](docs/open_questions.md): decisions on details not covered by the spec
- [CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md): rules for AI coding assistants working on this repository
