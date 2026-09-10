# Archivist App — Read-Only Testing

Production-safe testing for `archivist-app`. **No write or delete operations** — scripts, Cypress, and manual steps are GET/view only.

## Quick start (production post provision test)

### Prerequisites

- Azure CLI logged in: `az login`
- azd environment selected: `azd env select <env>` (e.g. `prod`)

URLs are **auto-resolved** by `resolve-test-env.ps1` from azd + Azure (no manual copy/paste required):

- `ARCHIVIST_APP_URL` ← `APP_GATEWAY_PUBLIC_URL` (azd)
- `ARCHIVIST_API_URL` ← AGW host when `ZERO_TRUST=true` (API at `/api/v1` on same host), else `*-api` web app from Azure CLI
- `ENTRA_CLIENT_ID` ← azd (optional; needed for authenticated API calls)

Preview resolved values:

```powershell
cd apps/archivist-app/testing
.\resolve-test-env.ps1
```

Override any value by setting env vars before running (explicit env wins over auto-resolve).

### Run all automated prod post provision test

```powershell
cd apps/archivist-app/testing
.\run-all-post-provision-test.ps1
```

Individual scripts:

| Script | Purpose |
|--------|---------|
| `resolve-test-env.ps1` | Auto-fetch AGW + API URLs from azd / Azure |
| `get-api-token.ps1` | `az account get-access-token` → `$env:ARCHIVIST_API_TOKEN` |
| `post-provision-test-api-readonly.ps1` | GET-only API endpoints (browse + tools) |
| `post-provision-test-routes.ps1` | AGW SPA route reachability (200 or auth redirect OK) |
| `run-all-post-provision-test.ps1` | Runs all of the above |

Skip flags: `.\run-all-post-provision-test.ps1 -SkipToken`, `-SkipApi`, `-SkipRoutes`

### Pass criteria (automated)

- **API**: `/health` and core browse GETs return 2xx; tool GETs return 2xx or expected 401/403 by role; no 5xx
- **Routes**: Listed SPA paths return 200 or auth redirect (not 404/502)

**ZERO_TRUST note:** From a developer laptop, AGW and API may return **403 (IP forbidden)**. That still proves the host is deployed; run the same scripts from VPN/jumpbox for full 2xx validation.

---

## Local pre-flight (before prod post provision test)

Run on every test session:

```powershell
cd apps/archivist-app
npm run lint
npx tsc --noEmit
npm run build
```

Cypress read-only E2E (requires dev server on port 5173):

```powershell
# Terminal 1 — optional: local API on 8000 for dev mock auth
cd apps/archivist-api
python app.py

# Terminal 2
cd apps/archivist-app
npm run dev

# Terminal 3 — from repo root
npm run cypress:run
```

---

## Manual prod browser matrix

Primary UI validation on **`ARCHIVIST_APP_URL`** (AGW). Sign in with Entra; use a separate account per persona.

### Personas

| Persona | Verify access | Verify blocked |
|---------|---------------|----------------|
| **No group** | Dashboard → Repos → Collection → Review (view); `/help/architecture` | Tools/Admin sidebar hidden; protected URLs → `?access_denied=true` |
| **Archivist** | + Correction requests, Data Ingestion (view only) | EPUB, Data Pipeline |
| **DataFoundations** | + Data Pipeline (view stats/jobs) | EPUB; Correction requests (unless also Archivist) |
| **Admin** | All routes render | Still **no write clicks** |

### Per-page read-only post provision test (~15–20 min each)

1. **Dashboard** `/` — stat cards; repo cards navigate
2. **Repositories** — list → repo detail → collection items → open review (**do not save**)
3. **Home** `/home` — filters, table, pagination (**no bulk select / ingest**)
4. **Review** — viewer, OCR/metadata panels, audit history (read-only if no edit role)
5. **Correction requests** — list, filters (**do not** acknowledge/dismiss)
6. **Data Pipeline** — stage table, jobs (**do not** trigger/terminate/rebuild)
7. **Data Ingestion** — status panels (**do not** retry/unpublish)
8. **EPUB Processor** — document list (**do not** upload/delete)
9. **Statistics Admin** — status display only (**do not** Clear/Rebuild)
10. **Architecture** `/help/architecture` — diagrams render
11. **Field mappings** `/admin/field-mappings` — list only (**do not** create/delete)

### Do not click (destructive UI)

- **Statistics Admin** — Clear / Rebuild buttons
- **Home** — ingest / bulk publish (UI not fully gated; backend should reject)
- **Collections items** — publish / unpublish
- **Review** — save metadata, OCR edits, status changes
- **Pipeline / Ingestion / EPUB** — any action buttons

---

## Cypress (local mocked E2E)

From repo root:

```powershell
npx cypress open                                    # interactive
npm run cypress:run                                 # headless read-only suite
$env:CYPRESS_REVIEW_DOCUMENT_ID="<existing-read-only-document-id>"
npx cypress run --spec "cypress/e2e/review/**/*.cy.js"  # live integration (optional)
```

Read-only suite: `cypress/e2e/readOnly/**/*.cy.ts` — mocks API, blocks POST/PATCH/PUT/DELETE.

---

## Execution order

1. Local pre-flight (lint, tsc, build, Cypress)
2. `run-all-post-provision-test.ps1` (AGW routes + API GET)
3. Manual browser matrix per persona on AGW URL
