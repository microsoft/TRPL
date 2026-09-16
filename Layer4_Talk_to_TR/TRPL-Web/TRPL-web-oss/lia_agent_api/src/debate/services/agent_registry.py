# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from autogen import AssistantAgent
from api.config import config
from debate.agents.camera import CameraAgent
from debate.agents.note_taker import NoteTakerAgent
from debate.agents.vote_recap_agent import VoteRecapAgent
from debate.agents.welcome import WelcomeAgent
from debate.agents.welcome.welcome_new_agent import WelcomeNewAgent
from debate.agents.engagement import TimeoutNudgeAgent
from debate.agents.scenario_agent import ScenarioAgent
from debate.agents.storys import StoryPickerAgent, HistorySummaryAgent
from debate.models.constants import Phase
from debate.scenarios.registry import ScenarioId, get_scenario
from pydantic import BaseModel, ConfigDict, computed_field

logger = logging.getLogger(f"lia.{__name__}")


STORYS_SELF_ROUTING_BLOCK = """

================== SELF-ROUTING — OVERRIDES OUTPUT SCHEMA ==================

This block OVERRIDES the MEMORY PROTOCOL's output schema. Every output
JSON object MUST now include the field "needs_more_kb" (boolean). Do
NOT omit it — the system depends on it for routing.

Your active_story / knowledge_context (when present) was retrieved for
the PREVIOUS visitor turn, not the current one. For the visitor's
CURRENT message you may not have the specifics they're asking about.

WHEN TO SET needs_more_kb=true:
  When the visitor names a CONCRETE subject that real records would do
  justice to — a specific person, event, place, policy, battle, letter,
  date, or a request to hear a particular story ("tell me about Alice",
  "what happened at the coal strike", "what were the Badlands like").
  If they point you at a topic where an authentic name, scene, date, or
  quote would make the answer real, search first rather than answer thin.
  Exception: set it false when that subject is already fully covered by
  your active_story / knowledge_context — then just answer from what you
  have.

WHAT TO PRODUCE in that case:
  - "needs_more_kb": true
  - "response": a brief acknowledgement, 8 words or fewer. VARY this every
    turn — do NOT reuse a phrase you used recently. Pick from (or invent
    in the same TR-voiced spirit):
      "Hmm — let me think on that."
      "Give me a moment to remember."
      "That one I'll have to dig for."
      "Hold on — I want to get it right."
      "Let me cast my mind back."
      "Bully, give me a beat to recall."
      "Stand by — I'm pulling the thread."
      "Now that's worth pausing on."
      "Easy now — let me think this through."
      "Hold the reins — I'll fetch it."
      "By George, that takes some thought."
      "Let me search my memory a moment."
  - Do NOT begin the substantive answer. The system will fetch
    knowledge and call you again with is_kb_retry=true.

WHEN TO SET needs_more_kb=false  (answer directly, in your own voice):
    - greetings and sign-offs ("hi", "hello", "how are you", "good to meet you")
    - light reactions / acknowledgements ("wow", "really?", "ok", "thanks",
      "yeah", "that's wild", 1-3 words of encouragement)
    - general opinions, values, reflections, or advice you can give from
      your own character ("what makes a good leader?", "what do you value
      most?", "any advice for a young person?")
    - light personal / in-character questions about who you are, your mood,
      or your tastes ("are you well?", "do you love the outdoors?")
    - follow-ups that add no new factual subject to look up
    - anything already covered by your active_story / knowledge_context
  Also false when the payload has is_kb_retry=true (knowledge was already
  fetched — answer now). In these cases answer fully and directly; do not
  stall for a search.

EXAMPLES (full JSON outputs):

  Visitor: "What was in your letter to Lodge on April 3, 1898?"
  Output: {
    "response": "Stand by — I'm pulling the thread.",
    "needs_more_kb": true,
    "target": "visitor",
    "participant_hint": null,
    "question_summary": null,
    "tracked_idea": null,
    "done": false,
    "memory": {}
  }

  Visitor: "Tell me about Alice."   (broad ask → still search first)
  Output: {
    "response": "Now that's worth pausing on.",
    "needs_more_kb": true,
    "target": "visitor",
    "participant_hint": null,
    "question_summary": null,
    "tracked_idea": null,
    "done": false,
    "memory": {}
  }

  Visitor: "Ha — you've got more fire than men half your age!"   (pure chit-chat → no search)
  Output: {
    "response": "Bully! A man ought to wear out, not rust out.",
    "needs_more_kb": false,
    "target": "visitor",
    "participant_hint": null,
    "question_summary": null,
    "tracked_idea": null,
    "done": false,
    "memory": {}
  }

  Visitor: "What makes a good leader?"   (general opinion → answer in your own voice, no search)
  Output: {
    "response": "Courage, and the will to act when others only talk. A leader takes the blows meant for the men behind them.",
    "needs_more_kb": false,
    "target": "visitor",
    "participant_hint": null,
    "question_summary": null,
    "tracked_idea": "what makes a leader",
    "done": false,
    "memory": {}
  }

  Visitor (with is_kb_retry=true in payload): "What was in your letter to Lodge on April 3, 1898?"
  Output: {
    "response": "I urged him plainly: act now or the chance is gone. Manila lay open and Dewey ready — I would not see hesitation cost us the moment.",
    "needs_more_kb": false,
    "target": "visitor",
    "participant_hint": null,
    "question_summary": null,
    "tracked_idea": null,
    "done": false,
    "memory": {}
  }

If the payload contains "is_kb_retry": true, knowledge has already been
fetched for you. Do NOT set needs_more_kb=true again — answer with what
you have, and be honest if the new context still doesn't cover the
specific ask ("I don't recall the particulars" — see GOVERNANCE).

If the payload contains "kb_unavailable": true, the knowledge fetch
came back empty. Do NOT invent a specific answer from training memory.
Say plainly that you cannot place the particulars and direct the
visitor to theodorerooseveltcenter.org. Keep it 2-3 sentences. Example:
  Visitor: "What did Quentin write in his last letter?"
  (with is_kb_retry=true, kb_unavailable=true)
  Output: {
    "response": "I couldn't place the particulars of that letter — I won't invent what I don't know. The Theodore Roosevelt Center keeps the family correspondence; that's where I'd send you.",
    "needs_more_kb": false,
    "target": "visitor",
    "participant_hint": null,
    "question_summary": null,
    "tracked_idea": null,
    "done": false,
    "memory": {}
  }

================================================================

"""


class AgentRegistry(BaseModel):
    """Registry holding all agents"""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    camera: CameraAgent
    welcome: WelcomeAgent
    welcome_new: WelcomeNewAgent
    scenario: ScenarioAgent
    # ``storys`` is the ADULT storys agent (kept under its legacy name for
    # backwards compat with any caller). ``storys_child`` is the CHILD
    # variant; the active one is chosen per-turn by ``storys_for_mode``.
    storys: ScenarioAgent | None = None
    storys_child: ScenarioAgent | None = None
    # VIP storys agent — honored-guest variant (prompt key "storys.vip").
    # Selected by VipNode, NOT by storys_for_mode.
    storys_vip: ScenarioAgent | None = None
    storys_default_mode: str = "adult"
    story_picker: StoryPickerAgent | None = None
    history_summary: HistorySummaryAgent | None = None
    timeout_nudges: dict[str, TimeoutNudgeAgent]
    note_taker: NoteTakerAgent
    vote_recap: VoteRecapAgent

    def timeout_nudge_for_phase(self, phase: str) -> TimeoutNudgeAgent:
        if phase in self.timeout_nudges:
            return self.timeout_nudges[phase]
        if Phase.scenario.value in self.timeout_nudges:
            return self.timeout_nudges[Phase.scenario.value]
        return next(iter(self.timeout_nudges.values()))

    def storys_for_mode(self, mode: str | None) -> ScenarioAgent | None:
        """Pick the storys agent matching the audience mode.
        Falls back to the adult agent when the requested variant is
        unavailable, so callers never need to null-check.
        """
        if mode == "child" and self.storys_child is not None:
            return self.storys_child
        return self.storys

    @computed_field
    def all(self) -> list[AssistantAgent]:
        """All agents in the registry"""
        agents = [
            self.camera,
            self.welcome,
            self.welcome_new,
            self.scenario,
            *self.timeout_nudges.values(),
            self.vote_recap,
        ]
        if self.storys is not None:
            agents.append(self.storys)
        if self.storys_child is not None:
            agents.append(self.storys_child)
        if self.storys_vip is not None:
            agents.append(self.storys_vip)
        if self.story_picker is not None:
            agents.append(self.story_picker)
        if self.history_summary is not None:
            agents.append(self.history_summary)
        return agents


async def get_agent_registry(
    scenario_id: ScenarioId | None = None,
    visitor_mode: str | None = None,
) -> AgentRegistry:
    """Build all agents for a session.

    visitor_mode: session-level default audience mode ("adult"/"child").
        Per-turn the StorysNode may override this using a camera-provided
        label on the active speaker. If visitor_mode is None we fall back
        to whatever the scenario definition provides (usually "adult").
    """
    mid_config = config.llm_config_mid.model_dump()
    large_config = config.llm_config_large.model_dump()
    resolved_scenario_id = scenario_id or ScenarioId.MIDNIGHT_RESERVES
    scenario = get_scenario(resolved_scenario_id)
    if not scenario:
        raise ValueError(f"Unknown scenario_id: {resolved_scenario_id}")

    timeout_nudge_phases = [Phase.welcome.value, Phase.scenario.value]

    # Build storys agents if this is the storytelling scenario. We build
    # BOTH adult and child variants every time so per-visitor switching is
    # a pure lookup at runtime — no rebuilds, no LLM warm-up latency.
    storys_agent_adult: ScenarioAgent | None = None
    storys_agent_child: ScenarioAgent | None = None
    storys_agent_vip: ScenarioAgent | None = None
    picker_agent: StoryPickerAgent | None = None
    history_agent: HistorySummaryAgent | None = None
    requested_mode = (visitor_mode or scenario.settings.get("visitor_mode") or "adult").lower()
    if requested_mode not in ("adult", "child"):
        requested_mode = "adult"
    if resolved_scenario_id == ScenarioId.STORYS:
        from debate.scenarios.story_rag import get_index
        from debate.services.prompt_overrides import get_effective

        story_index = get_index()
        compact_index = story_index.build_prompt_index()

        adult_prompt, adult_overridden = get_effective("storys.adult")
        child_prompt, child_overridden = get_effective("storys.child")
        vip_prompt, vip_overridden = get_effective("storys.vip")
        picker_prompt, picker_overridden = get_effective("storys.picker")
        if adult_overridden or child_overridden or vip_overridden or picker_overridden:
            logger.info(
                "storys prompt overrides active: adult=%s child=%s vip=%s picker=%s",
                adult_overridden,
                child_overridden,
                vip_overridden,
                picker_overridden,
            )

        routing_block = STORYS_SELF_ROUTING_BLOCK if config.storys_self_routing else ""
        if config.storys_self_routing:
            logger.info("storys self-routing ENABLED")

        storys_agent_adult = ScenarioAgent(
            llm_config=large_config,
            system_message=adult_prompt + "\n\n" + compact_index,
            post_protocol_suffix=routing_block,
        )
        storys_agent_child = ScenarioAgent(
            llm_config=large_config,
            system_message=child_prompt + "\n\n" + compact_index,
            post_protocol_suffix=routing_block,
        )
        # VIP: honored-guest variant (warmer/looser prompt). Built every time
        # alongside adult/child so the VIP phase is a pure lookup at runtime.
        storys_agent_vip = ScenarioAgent(
            llm_config=large_config,
            system_message=vip_prompt + "\n\n" + compact_index,
            post_protocol_suffix=routing_block,
        )
        # Picker: selects story + generates KB query (mid model)
        picker_agent = StoryPickerAgent(
            llm_config=mid_config,
            story_index_text=compact_index,
            system_prompt=picker_prompt,
        )
        # History: summarizes visitor conversations (mid model)
        history_agent = HistorySummaryAgent(llm_config=mid_config)

        timeout_nudge_phases.append(Phase.storys.value)

    timeout_nudges = {
        phase: TimeoutNudgeAgent(llm_config=mid_config, phase=phase)
        for phase in timeout_nudge_phases
    }

    return AgentRegistry(
        camera=CameraAgent(llm_config=large_config),
        welcome=WelcomeAgent(llm_config=large_config),
        welcome_new=WelcomeNewAgent(llm_config=large_config),
        scenario=ScenarioAgent(
            llm_config=large_config,
            system_message=scenario.scenario_agent_system_message,
        ),
        storys=storys_agent_adult,
        storys_child=storys_agent_child,
        storys_vip=storys_agent_vip,
        storys_default_mode=requested_mode,
        story_picker=picker_agent,
        history_summary=history_agent,
        timeout_nudges=timeout_nudges,
        note_taker=NoteTakerAgent(llm_config=mid_config),
        vote_recap=VoteRecapAgent(llm_config=mid_config),
    )
