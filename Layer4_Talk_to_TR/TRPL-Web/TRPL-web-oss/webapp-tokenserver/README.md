# Web App Package — token_server + Avatar SPA

This is the **frontend-facing** half of the TR avatar system. Deploy this to
Azure App Service (Web App, Linux, Python 3.12).

## Provider-free local mode

The repository-root `layer4` Compose profile serves the static pages on
<http://127.0.0.1:8002> with `TALK_TO_TR_RUNTIME_MODE=deterministic`. The health
endpoint reports LiveKit as unavailable, `/api/token` returns `503`, and
provider-backed admin proxies return `503`; no fake LiveKit or avatar success is
returned.

Use the shared setup and verifier in
[`Layer4_Talk_to_TR/LOCAL_DEVELOPMENT.md`](../../../LOCAL_DEVELOPMENT.md).
Cloud mode remains the default and requires LiveKit signing credentials at
startup.

## Files

| File | Purpose |
|---|---|
| `token_server.py` | FastAPI: signs LiveKit JWT tokens, serves `index.html`. |
| `index.html` | Browser SPA. Talks to LiveKit Cloud directly. |
| `requirements.txt` | Minimal deps — no LiveKit agent plugins here. |
| `.env` | Only `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`. **Move these to Application Settings instead of keeping the file.** |
| `startup.sh` | Startup command for Azure. |

## Deploy (Azure CLI)

```bash
RG=<your-resource-group>
APP_NAME=tr-avatar-token-<unique>    # must be globally unique in *.azurewebsites.net
PLAN=<your-app-service-plan>         # Basic B1 or higher, Linux

# Create the Web App (if plan doesn't exist, see `az appservice plan create` first)
az webapp create \
  --resource-group $RG \
  --plan $PLAN \
  --name $APP_NAME \
  --runtime "PYTHON:3.12"

# Enable WebSockets (harmless here, but future-proof)
az webapp config set \
  --resource-group $RG \
  --name $APP_NAME \
  --web-sockets-enabled true \
  --always-on true

# Set the three LiveKit secrets as Application Settings
az webapp config appsettings set \
  --resource-group $RG --name $APP_NAME --settings \
    LIVEKIT_URL="wss://<your-livekit-project>.livekit.cloud" \
    LIVEKIT_API_KEY="<your-key>" \
    LIVEKIT_API_SECRET="<your-secret>"

# Startup command
az webapp config set \
  --resource-group $RG --name $APP_NAME \
  --startup-file "bash startup.sh"

# Deploy the zip
zip -r deploy.zip . -x '*.git*'
az webapp deploy \
  --resource-group $RG --name $APP_NAME \
  --src-path deploy.zip --type zip
```

Your avatar page will be at:

```
https://$APP_NAME.azurewebsites.net/
```

Open that in a browser — HTTPS is automatic (valid Azure-managed cert), so the
microphone permission will work.

## Notes

- **Do NOT put the .env file in git or public storage** — it has signing
  secrets. Prefer Application Settings above.
- The browser needs outbound WSS to `$LIVEKIT_URL`. Standard corporate /
  household networks allow this.
- This Web App does **not** talk to `lia_agent_api` at all. The brain is
  reached only by the livekit_worker on the VM, via LiveKit Cloud.
