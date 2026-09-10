## Correction intake test caller (dev)

This folder contains a tiny, dependency-free script you can use to simulate a downstream caller (e.g. Reading Room) and POST a correction request into the Archivist API.

It is meant for **dev verification** while the real downstream integration is still being built.

### What you need

- **Dev API URL**: `https://<archivist-api-host>`
- A valid **Bearer access token** for the correction intake endpoint
  - This requires `CORRECTION_INTAKE_AUDIENCE` to be configured on the API App Service
  - And your caller app id (`azp`/`appid`) to be allowlisted in `CORRECTION_INTAKE_SOURCE_MAP`

### Run (Node 18+)

PowerShell example:

```powershell
$env:API_BASE_URL="https://<archivist-api-host>"
$env:ACCESS_TOKEN="<paste access token here>"
$env:RECORD_ID="<uuid>"
$env:NOTE="Test correction note from dummy caller"
node .\tools\correction-intake-test-caller\post_correction_request.mjs
```

### Expected responses

- `201` when accepted
- `401/403` when the token is missing/invalid or the caller is not registered
- `503` when intake auth is not configured (missing tenant/audience)
