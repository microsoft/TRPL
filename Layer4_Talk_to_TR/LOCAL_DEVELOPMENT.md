# Layer 4 local development

The `layer4` Compose profile runs the Talk to TR HTTP and WebSocket contracts
without cloud providers, cameras, GPUs, model weights, visitor media, or licensed
avatar assets.

## Start and verify

From the repository root:

```bash
python3 Layer1_Data_foundations/scripts/local_dev.py configure
docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile layer4 up --build -d
python3 Layer4_Talk_to_TR/scripts/verify_local_stack.py
```

The profile exposes:

| Service | URL | Local behavior |
| --- | --- | --- |
| `layer4-brain` | <http://127.0.0.1:8010> | Authenticated sessions, WebSockets, and camera event intake |
| `layer4-token-server` | <http://127.0.0.1:8002> | Static visitor/admin pages and explicit provider-unavailable responses |

`local_dev.py configure` writes matching ignored API/admin keys to owner-only
`.env.local` files. It also prepares owner-only ignored guardrail file locations;
it does not generate or publish guardrail values.

The verifier checks:

- brain and token-server readiness
- missing and invalid API-key rejection
- single-use WebSocket controller authentication
- deterministic session startup and ping/pong transport
- fictional, no-media camera event routing into the target WebSocket session
- static page availability
- explicit `503` responses for LiveKit and provider-backed admin operations

## Replay the fictional camera fixture

The tracked fixture contains only synthetic event metadata:

```bash
export LIA_API_KEY="$(
  sed -n 's/^CLIENT_API_KEYS=//p' \
    Layer4_Talk_to_TR/TRPL-Web/TRPL-web-oss/lia_agent_api/.env.local
)"
python3 \
  Layer4_Talk_to_TR/TRPL-CameraPlatform/camera_platform/scripts/replay_events.py \
  --dir Layer4_Talk_to_TR/TRPL-CameraPlatform/camera_platform/fixtures \
  --file local-events.jsonl \
  --url http://127.0.0.1:8010
```

Without a `session_id`, the brain creates a deterministic camera-phase session.
Use the verifier when the camera event must also be proven on an authenticated
WebSocket.

## Runtime boundaries

`LIA_RUNTIME_MODE=deterministic` and
`TALK_TO_TR_RUNTIME_MODE=deterministic` are explicit local modes. They never
fabricate LLM, RAG, speech, LiveKit, or avatar output. Provider availability is
reported in `/healthz`, and provider-backed routes return `503`.

Cloud mode remains the default. The brain fails at startup when its required LLM
configuration is absent; the token server fails when LiveKit signing credentials
are absent.

## Cloud text and retained-data acceptance

The separate `layer4-cloud` profile exposes the cloud-text brain at
<http://127.0.0.1:8011>. It reuses the working Layer 3 Azure OpenAI and Azure
Search settings without replacing the minimal deterministic image. It does not
enable speech, LiveKit, an avatar, or a camera.

First populate the ignored deployment-owned files:

```text
Layer4_Talk_to_TR/TRPL-Web/TRPL-web-oss/lia_agent_api/private/guardrails/roosevelt-guardrails.txt
Layer4_Talk_to_TR/TRPL-Web/TRPL-web-oss/lia_agent_api/private/guardrails/threat-patterns.json
```

Then configure, start, and verify from the repository root:

```bash
python3 Layer4_Talk_to_TR/scripts/configure_cloud_from_layer3.py
docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile layer4-cloud up --build -d layer4-brain-cloud
docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile layer4-cloud exec -T layer4-brain-cloud \
  python scripts/verify_cloud_data.py
```

The configuration helper writes an owner-only ignored `.env.cloud.local`; it
does not print credentials. The verifier checks the cloud provider state,
queries the retained fictional EPUB from the Layer 2 book index, opens an
authenticated `storys` WebSocket session, and requires the generated answer to
use the retained Blue Heron Compact facts.

Layer 4 cloud text acceptance still is not production guardrail acceptance.

## Local LiveKit and avatar-free Azure Speech

The isolated `layer4-live` profile adds:

- self-hosted LiveKit `v1.13.5` on `ws://127.0.0.1:7880`
- a cloud-mode token/UI server at <http://127.0.0.1:8003>
- an avatar-free worker using standard Azure Speech
- the existing cloud-text brain and retained Layer 2 Azure Search data
- ignored synthetic admin, guardrail, and story fixtures

It does not use LemonSlice or a licensed persona. Azure TTS audio is published
directly to the LiveKit room.

Generate owner-only settings, start the services, and run the automated media
acceptance:

```bash
export LAYER4_AZURE_AI_RESOURCE_GROUP="<resource-group>"
export LAYER4_AZURE_AI_ACCOUNT="<azure-ai-account>"
# Optional when the Azure CLI default subscription already contains the account:
export AZURE_SUBSCRIPTION_ID="<subscription-id>"

python3 Layer4_Talk_to_TR/scripts/configure_live_acceptance.py
docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile layer4-live up --build -d \
  layer4-brain-cloud layer4-livekit \
  layer4-token-server-live layer4-live-worker
python3 Layer4_Talk_to_TR/scripts/verify_live_media.py
```

The verifier creates a real LiveKit participant, sends a text turn through the
worker and brain, requires an Azure-TTS audio track, and sends a synthetic image
to the `gpt-4.1-mini-vision` deployment in the configured Azure AI Services
account.

For a manual test, open <http://127.0.0.1:8003>, allow microphone access, and
click the start control. Confirm that:

1. The page joins a `tr-avatar-*` room without connection errors.
2. The initial greeting is audible even though no avatar video appears.
3. Spoken input is transcribed and receives an audible answer.
4. Text chat such as `What happened at the fictional Cedar Station?` receives
   an answer and audio.
5. <http://127.0.0.1:8003/admin> requires the generated admin token from the
   ignored `webapp-tokenserver/.env.live.local`.

The generated guardrails and Cedar Station corpus are clearly fictional,
acceptance-only data. They prove configuration loading and control flow; they
are not approved production safety policy or historical content.

## Containerized camera acceptance

The `layer4-camera` profile is separate because the image is large and a real
camera source is host-specific. Prepare the pinned ignored model and local
settings:

```bash
python3 Layer4_Talk_to_TR/scripts/configure_live_acceptance.py
python3 Layer4_Talk_to_TR/scripts/prepare_camera_model.py
docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile layer4-camera up --build -d \
  layer4-brain-cloud layer4-camera
```

The camera dashboard is <http://127.0.0.1:8084>. The container uses CPU
inference (`YOLO_DEVICE=-1`), disables cross-camera ReID downloads, validates
the Azure camera VLM at startup, and authenticates HTTP camera events to the
brain.

Docker Desktop on macOS does not expose the host webcam as `/dev/video0`.
Publish the MacBook camera from a host terminal:

```bash
CAMERA_STREAM_BIND=0.0.0.0 \
CAMERA_STREAM_ALLOW_NETWORK=true \
  Layer4_Talk_to_TR/scripts/start_macos_camera_stream.sh
```

The script uses AVFoundation camera `0` at 1280x720/30 FPS and keeps running
until you press **Ctrl-C**. macOS may ask Terminal for Camera permission. Its
secure default binds only to loopback; Docker Desktop requires the explicit
network-visible opt-in above. Use it only on a trusted network with the macOS
firewall enabled, and stop it immediately after testing. Pass a different
AVFoundation device index as the first argument when needed.

The container's default source already targets this host stream:

```bash
export LAYER4_CAMERA_SOURCE=http://host.docker.internal:8090/stream.mjpg
python3 Layer4_Talk_to_TR/scripts/configure_live_acceptance.py
```

Then open the dashboard and click **Use Camera**. This button stops an active
uploaded video, restores the configured host stream, and starts camera capture.
Confirm that the stream appears, people receive pose boxes, and camera events
reach the brain. Direct USB passthrough, LemonSlice/avatar behavior, and GPU
throughput are intentionally outside this local acceptance profile.

If no host/network stream is running, use **Choose Video** in the dashboard
before clicking **Start**. The dashboard uploads the video into the camera
container and uses it as the acceptance input. Starting without either an
uploaded video or a reachable MJPEG/RTSP source returns the configured-source
error instead of briefly showing a false running state.

The physical camera pipeline is separate from the default profile. Its real
acceptance requires approved hardware, calibrated zones, model weights, and the
camera platform's Python environment. The deterministic fixture is the supported
hardware-free contract test.

## Stop

```bash
docker compose --env-file Layer1_Data_foundations/.env.local \
  --profile layer4 --profile layer4-cloud \
  --profile layer4-live --profile layer4-camera down
```
