# -*- coding: utf-8 -*-
import logging
import re
from datetime import datetime

from debate.models.constants import Phase, TR_SPEAKER
from debate.models.state import DebateState, HistoryEntry
from debate.services.agent_registry import AgentRegistry
from debate.services.io import IOService

logger = logging.getLogger(f"lia.{__name__}")


def _build_recent_phase_history(state: DebateState, phase: str, limit: int = 6) -> list[dict]:
    recent = [
        {
            "speaker": entry.speaker,
            "text": entry.text,
            "target": entry.target,
        }
        for entry in state.history
        if entry.phase == phase
    ]
    if limit <= 0:
        return recent
    return recent[-limit:]


def _mentions_disallowed_name(
    response: str,
    *,
    participants: list[str],
    speakers: list[str],
) -> bool:
    allowed = {name for name in speakers}
    disallowed = [name for name in participants if name not in allowed]
    for name in disallowed:
        if not name:
            continue
        if re.search(rf"\b{re.escape(name)}\b", response, flags=re.IGNORECASE):
            return True
    return False


async def send_timeout_nudge(
    io: IOService,
    agent_registry: AgentRegistry,
    state: DebateState,
    phase: str,
    last_prompt: str | None,
    participants: list[str],
    eligible_speakers=None,
    *,
    retry_stage: str | None = None,
    retry_index: int | None = None,
    guidance: str | None = None,
    fallback_text: str | None = None,
) -> str | None:
    """Generate and send a timeout nudge. Returns the nudge text or None."""
    if phase in {Phase.scenario_vote.value, Phase.brainstorm_vote.value}:
        logger.info("Skipping timeout nudge during vote phase: %s", phase)
        return None

    timeout_agent = agent_registry.timeout_nudge_for_phase(phase)
    recent_history = _build_recent_phase_history(state, phase)
    speakers = state.speaking_participants()

    parsed = await timeout_agent.generate_response(
        last_prompt=last_prompt,
        participants=participants,
        speakers=speakers,
        recent_history=recent_history,
        retry_stage=retry_stage,
        retry_index=retry_index,
        guidance=guidance,
    )

    response = (parsed.get("response") or "").strip()
    if not response and fallback_text:
        response = fallback_text.strip()

    if response and _mentions_disallowed_name(
        response,
        participants=participants,
        speakers=speakers,
    ):
        logger.warning(
            "Timeout nudge referenced non-speaker name; using non-address fallback (phase=%s)",
            phase,
        )
        response = (
            fallback_text.strip()
            if fallback_text
            else "I am ready for your counsel. Who wishes to speak?"
        )

    if not response:
        logger.warning("TimeoutNudgeAgent returned empty response")
        return None

    state.history.append(
        HistoryEntry(
            speaker=TR_SPEAKER,
            text=response,
            audience="all",
            phase=phase,
            timestamp=datetime.now(),
            target=None,
        )
    )

    await io.output.send_output(
        text=response,
        waiting_for_input=True,
        phase=phase,
        eligible_speakers=eligible_speakers,
    )

    return response
