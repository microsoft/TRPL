# Talk to TR web services

This directory contains the web and real-time services used by the Talk to TR
reference experience:

| Component | Purpose |
| --- | --- |
| `lia_agent_api/` | FastAPI conversation brain, session state, prompts, RAG integration, and camera-event intake |
| `livekit_worker/` | LiveKit worker for speech, audio, optional avatar integration, and the LIA bridge |
| `webapp-tokenserver/` | LiveKit token service and visitor/admin static pages |
| `pose_tragger/` | Optional LemonSlice pose-trigger utility |

The repository-root Compose profiles are the maintained local setup. Follow
[`Layer4_Talk_to_TR/LOCAL_DEVELOPMENT.md`](../../LOCAL_DEVELOPMENT.md) for:

- deterministic operation without cloud providers or licensed assets;
- cloud text and retained-data acceptance;
- self-hosted LiveKit and avatar-free Azure Speech;
- optional camera/VLM acceptance; and
- shutdown and verification commands.

## External inputs

Production deployments must provide their own:

- Azure AI, Search, and Speech configuration;
- LiveKit credentials and deployment;
- licensed avatar/persona service and assets, when enabled;
- story/RAG corpus and `this_day_in_history.json`;
- guardrails and evaluation configuration; and
- infrastructure, monitoring, identity, and network controls.

These values and content packs are deployment-owned and intentionally excluded
from Git. The Compose helpers write ignored local configuration and mount
private content at runtime.

## Direct component development

Each component has its own requirements and example configuration:

- [`lia_agent_api/README.md`](lia_agent_api/README.md)
- [`livekit_worker/README.md`](livekit_worker/README.md)
- [`webapp-tokenserver/README.md`](webapp-tokenserver/README.md)
- [`pose_tragger/readme.md`](pose_tragger/readme.md)

The source is published as an implementation showcase. No feature development
is planned; security maintenance will preserve the documented interfaces and
local workflows.
