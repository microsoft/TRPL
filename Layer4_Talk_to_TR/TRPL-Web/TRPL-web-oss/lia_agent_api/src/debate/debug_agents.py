# -*- coding: utf-8 -*-
import asyncio
import json
import logging
import random

from api.config import config
from pydantic import ValidationError

from debate.models.sockets import (
    DebateOutputEvent,
    InboundSocketMessage,
    ParticipantInput,
    SessionCompleteEvent,
    SpokenInput,
    TouchscreenPrompt,
    debug_agent_output_event_adapter,
)
from debate.models.state import Participant, DebateState
from debate.utils import build_agent

logger = logging.getLogger(f"lia.{__name__}")


class DebugAgent:
    """Agent that simulates a human participant in a debate session"""

    def __init__(
        self,
        participant: Participant,
        debate_state: DebateState,
        input_queue: asyncio.Queue[InboundSocketMessage],
        llm_config: dict,
        session_id: str = None,
        agent_type: str = "informed",
    ):
        self.participant = participant  # name, camp, reason
        self.debate_state = debate_state
        self.input_queue = input_queue
        self.session_id = session_id
        self.agent_type = agent_type
        self.llm_config = llm_config  # Store for rebuilding agent
        self.running = False
        self._task: asyncio.Task | None = None
        self._has_received_followup = False  # Track if we've received a follow-up
        self._last_phase = getattr(debate_state, 'phase', None)  # Track last phase to detect phase changes
        self._stream_buffers: dict[str, str] = {}
        self._local_utterance_counter = -1

        # Create LLM agent for generating text responses
        # System message will be rebuilt dynamically based on follow-up status
        system_message = self._build_system_message()
        self.agent = build_agent(
            f"DebugAgent_{participant.name}", system_message, llm_config
        )
        logger.debug(
            f"DebugAgent {participant.name} initialized with agent_type={agent_type}, "
            f"system_message preview: {system_message[:200]}..."
        )

    def _next_participant_utterance_id(self) -> str:
        self._local_utterance_counter += 1
        return f"P{self._local_utterance_counter:07d}"

    def _is_followup_question(self, prompt_text: str) -> bool:
        """Check if this is a follow-up question by examining debate history"""
        # Check if we're the last speaker (meaning we just spoke and might get a follow-up)
        if self.debate_state.last_speaker != self.participant.name:
            return False

        # Check if the most recent history entry indicates a follow-up
        if not self.debate_state.history:
            return False

        # Find the most recent entry where this participant spoke
        last_participant_idx = -1
        for i in range(len(self.debate_state.history) - 1, -1, -1):
            if self.debate_state.history[i].speaker == self.participant.name:
                last_participant_idx = i
                break

        if last_participant_idx == -1:
            # This participant hasn't spoken yet, so this can't be a follow-up
            return False

        # See if another participant has spoken since this participant
        for i in range(last_participant_idx + 1, len(self.debate_state.history)):
            entry = self.debate_state.history[i]
            if (
                entry.speaker != self.participant.name
                and entry.speaker in self.debate_state.roster
            ):
                return False
        return True

    def _build_system_message(self, is_followup: bool = False) -> str:
        """Build system message for the debug agent"""
        current_phase = str(getattr(self.debate_state, "phase", "") or "")
        is_welcome_phase = current_phase == "welcome"
        is_scenario_phase = current_phase in {
            "scenario",
            "scenario_vote",
            "brainstorm_vote",
        }

        base_info = f"You are {self.participant.name} participating in a debate moderated by President Theodore Roosevelt."
        if is_welcome_phase:
            base_info = f"You are {self.participant.name} speaking with Theodore Roosevelt during the welcome phase before a debate session."

        if self.agent_type == "curious_museum_visitor":
            if is_welcome_phase:
                followup_note = (
                    " Since this is a follow-up prompt, give a brief response and then pivot to a different TR topic."
                    if is_followup
                    else ""
                )
                return f"""You are {self.participant.name}, a curious museum visitor speaking with Theodore Roosevelt.
You are eager to learn more about TR as a person, his values, and his choices.
Keep things very light, friendly, and curious.
Keep responses concise (1-2 sentences, occasionally 3).
Usually ask TR a question.
Usually pick a new TR-related topic instead of following up.
Follow-up questions are rare and allowed at most once.
Only rarely answer TR's question directly; most of the time ignore the question and ask TR a new question.{followup_note}
Avoid modern political jargon and avoid taking strong policy positions in this phase."""

            if is_scenario_phase:
                followup_note = (
                    " A follow-up was asked, so address it clearly and add one supporting reason."
                    if is_followup
                    else ""
                )
                return (
                    base_info
                    + f"""
Respond like a reasonably engaged participant with a high school education level.
Use clear, everyday language, stay on topic, and give practical reasons for your view.
Keep responses concise (1-3 sentences) and contribute one clear point at a time.{followup_note}
Be thoughtful and respectful, and avoid extreme certainty or expert-level technical claims."""
                )

        # If this is a follow-up question, all agents should respond appropriately
        if is_followup:
            return (
                base_info
                + f"""
You have been asked a follow-up question to help clarify or improve your previous response.
Now respond naturally and authentically based on your position. 
Keep your responses concise (1-3 sentences) and stay true to your camp and reason.
Address the follow-up question directly and provide a thoughtful, on-topic response.
"""
            )

        # Agent type-specific behavior for initial responses
        if self.agent_type == "uninformed":
            return (
                base_info
                + """
When asked questions or prompted to speak, give responses that are inadequate in some way.
You are not being deliberately difficult - you just don't know much and aren't very engaged.
Responses should range from a few words to one sentence.
Your answers should be unsatisfying in one of these ways:
- irrelevant to the question at hand
- vague
- adding no new information

However, when asked a follow-up question, you may begin to engage more fully and provide a more thoughtful response.
Do your best to answer one part of each question while keeping it short.
"""
            )
        elif self.agent_type == "malicious_disruptive":
            return (
                base_info
                + """
When asked questions or prompted to speak, respond inappropriately or disruptively.
You may use:
- Personal attacks or offensive language
- Vulgar or inappropriate comments
- Obviously trolling or intentionally disruptive content
- Content that is clearly inappropriate for a historical debate

However, when asked a follow-up question that redirects you, you should recognize the redirect and respond appropriately with legitimate, respectful content that addresses the actual question.
"""
            )
        elif self.agent_type == "disengaged":
            return (
                base_info
                + """
When asked questions or prompted to speak, give very brief, non-committal responses.
Examples of your typical responses:
- "not sure"
- "I don't know"
- "whatever"
- "I guess"
- Very short, dismissive answers

However, when asked a follow-up question that encourages you to engage, you should provide a more substantive response that addresses the question.
"""
            )
        elif self.agent_type == "struggling":
            return (
                base_info
                + """
When asked questions or prompted to speak, express confusion or lack of understanding.
Examples of your typical responses:
- "I don't really know what to say here"
- "I'm confused about this"
- "I'm not sure I understand"
- Express uncertainty and confusion about the topic

However, when asked a follow-up question that offers clarification, you should use that help to provide a clearer, more confident response.
"""
            )
        elif self.agent_type == "off_topic":
            return (
                base_info
                + """
When asked questions or prompted to speak, go off-topic or incorrectly apply modern/real-world issues that don't relate to the current debate.
Examples:
- Bringing up modern tariffs, policies, or events that aren't relevant to the current discussion
- Talking about completely unrelated topics
- Applying concepts incorrectly to the current debate topic

However, when asked a follow-up question that redirects you to the core issue and current debate topic, you should refocus and provide a response that addresses the actual question being discussed.
"""
            )
        elif self.agent_type == "on_track_needs_more":
            return (
                base_info
                + """
When asked questions or prompted to speak, give valid responses that repeat your core reason without elaboration.
Your responses should:
- Be on-topic and relevant
- Restate your position without adding depth
- Lack specific examples or deeper reasoning
- Be brief and surface-level

However, when asked a follow-up question that asks for elaboration, you should provide more depth, examples, and detailed reasoning.
"""
            )
        elif self.agent_type == "8_year_old":
            return (
                base_info
                + """
When asked questions or prompted to speak, respond like an 8-year-old child would.
Your responses should:
- Use simple, age-appropriate language and vocabulary
- Be shorter and more direct (1-2 sentences typically)
- Focus on concrete, tangible concepts rather than abstract reasoning
- Express thoughts in a straightforward, sometimes emotional way
- Show limited understanding of complex historical or political contexts
- May ask simple questions or express confusion about complicated topics
- Use simpler sentence structures

However, when asked a follow-up question that simplifies or clarifies the topic, you should engage more and provide a response that shows you understand the basic point, even if expressed simply.
"""
            )
        else:  # "informed" (default)
            return (
                base_info
                + f"""
When asked questions or prompted to speak, respond naturally and authentically based on your position. 
Keep your responses concise (1-3 sentences) and limit your responses to one key point at a time.
Stay true to your camp and reason.
You are participating in a thoughtful discussion, so be respectful but firm in your views."""
            )

    async def start(self, output_queue: asyncio.Queue):
        """Start monitoring the output queue for prompts directed at this agent"""
        if self.running:
            logger.warning(f"DebugAgent for {self.participant.name} already running")
            return

        self.running = True
        self._output_queue = output_queue
        self._task = asyncio.create_task(self._monitor_outputs(output_queue))
        logger.info(
            f"DebugAgent started for participant {self.participant.name} with agent_type={self.agent_type}"
        )

    async def stop(self):
        """Stop monitoring and clean up"""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"DebugAgent stopped for participant {self.participant.name}")

    async def _monitor_outputs(self, output_queue: asyncio.Queue):
        """Monitor output queue for messages that require this agent's response"""
        try:
            while self.running:
                try:
                    # Get event from output queue (with timeout to allow checking running flag)
                    raw_event = await asyncio.wait_for(output_queue.get(), timeout=1.0)

                    if not isinstance(raw_event, dict):
                        continue

                    try:
                        event = debug_agent_output_event_adapter.validate_python(
                            raw_event
                        )
                    except ValidationError:
                        # Debug agents only care about debate_output/session_complete.
                        continue

                    # Check if this is a message we should respond to
                    if isinstance(event, DebateOutputEvent):
                        await self._handle_debate_output_event(event)

                    # Check if session is complete
                    if isinstance(event, SessionCompleteEvent):
                        logger.info(
                            f"Session complete, stopping agent for {self.participant.name}"
                        )
                        break

                except asyncio.TimeoutError:
                    # Continue loop to check running flag
                    continue
                except Exception as e:
                    logger.error(
                        f"Error in DebugAgent {self.participant.name}: {e}",
                        exc_info=True,
                    )

        except asyncio.CancelledError:
            logger.info(f"DebugAgent monitoring cancelled for {self.participant.name}")
        except Exception as e:
            logger.error(
                f"Fatal error in DebugAgent {self.participant.name}: {e}",
                exc_info=True,
            )

    async def _handle_debate_output(self, event: DebateOutputEvent):
        """Handle a debate_output event and respond if needed"""
        # Only respond if waiting for input
        if not event.waiting_for_input:
            return

        # Check for touchscreen prompts directed at this participant
        touchscreen_prompts = event.touchscreen_prompts
        touchscreen_response = None

        for prompt in touchscreen_prompts:
            if prompt.participant_id == self.participant.name:
                # This prompt is for us
                touchscreen_response = self._choose_touchscreen_response(prompt)
                break

        # Check if text prompt is directed at this participant
        # First check speaker_prompt.participant_id (authoritative source)
        speaker_prompt_participant = (
            event.speaker_prompt.participant_id if event.speaker_prompt else None
        )

        text = event.text or ""
        text_response = None

        # Only respond if speaker_prompt.participant_id matches this participant
        if text and speaker_prompt_participant == self.participant.name:
            text_response = await self._generate_text_response(text)

        # Submit response if we have one
        if touchscreen_response or text_response:
            if not await self._wait_for_audio_idle(timeout=config.debug_agent_audio_idle_timeout):
                logger.info(
                    f"DebugAgent {self.participant.name} proceeding despite audio still playing (timeout)"
                )
            await self._submit_response(text_response, touchscreen_response)

    async def _handle_debate_output_event(self, event: DebateOutputEvent) -> None:
        streaming = event.streaming is True
        utterance_id = event.utterance_id
        if streaming and utterance_id is not None:
            chunk = event.text or ""
            if chunk:
                self._stream_buffers[utterance_id] = self._stream_buffers.get(utterance_id, "") + chunk

            if event.stream_end is True:
                full_text = self._stream_buffers.pop(utterance_id, "")
                finalized = event.model_copy(
                    update={
                        "text": full_text,
                        "streaming": False,
                        "stream_end": False,
                    }
                )
                await self._handle_debate_output(finalized)
            return

        if utterance_id is not None:
            self._stream_buffers.pop(utterance_id, None)
        if not streaming:
            await self._handle_debate_output(event)

    async def _wait_for_audio_idle(self, timeout: float) -> bool:
        if self.debate_state.inputs.audio_idle:
            return True
        loop = asyncio.get_running_loop()
        start = loop.time()
        while loop.time() - start < timeout:
            if self.debate_state.inputs.audio_idle:
                return True
            await asyncio.sleep(0.1)
        return False

    def _is_prompted_for_text_response(self, text: str) -> bool:
        """Check if the text prompt is asking this participant to respond"""
        # Simple heuristic: check if the participant's name is mentioned
        # and the text is asking a question or requesting input
        name_lower = self.participant.name.lower()
        text_lower = text.lower()

        # Check if name is mentioned
        if name_lower not in text_lower:
            return False

        # Check for question patterns or direct requests
        question_indicators = [
            "?",
            "please share",
            "please explain",
            "why did",
            "why do",
            "what do you",
            "how do you",
            "share your thoughts",
        ]

        return any(indicator in text_lower for indicator in question_indicators)

    async def _generate_text_response(self, prompt_text: str) -> str | None:
        """Generate a text response using the LLM agent"""
        try:
            # Check if phase has changed
            current_phase = getattr(self.debate_state, 'phase', None)
            phase_changed = current_phase != self._last_phase
            if phase_changed:
                self._last_phase = current_phase
                logger.info(
                    f"DebugAgent {self.participant.name} detected phase change to {current_phase}"
                )

            # Check if this is a follow-up question
            is_followup = self._is_followup_question(prompt_text)
            logger.debug(
                f"DebugAgent {self.participant.name} (type={self.agent_type}) checking follow-up: is_followup={is_followup}, has_received_followup={self._has_received_followup}"
            )

            # Rebuild agent if phase changed or if this is a follow-up and we haven't updated yet
            should_rebuild = False
            if phase_changed:
                should_rebuild = True
                logger.info(
                    f"DebugAgent {self.participant.name} rebuilding agent due to phase change"
                )
            elif is_followup and not self._has_received_followup:
                self._has_received_followup = True
                should_rebuild = True
                logger.info(
                    f"DebugAgent {self.participant.name} (type={self.agent_type}) switching to follow-up mode"
                )

            if should_rebuild:
                # Rebuild the agent with updated system message
                from debate.utils import build_agent

                system_message = self._build_system_message(is_followup=is_followup)
                self.agent = build_agent(
                    f"DebugAgent_{self.participant.name}",
                    system_message,
                    self.llm_config,
                )

            # Build context for the agent
            context = {
                "prompt": prompt_text,
                "debate_history": [
                    entry.model_dump(exclude={"timestamp"})
                    for entry in self.debate_state.history
                ],
            }

            # Generate response using threaded wrapper to prevent event loop blocking
            from debate.utils import _a_generate_reply_threaded

            reply = await _a_generate_reply_threaded(
                self.agent,
                messages=[
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)}
                ],
            )

            if reply:
                # Clean up the response (remove any JSON formatting if present)
                response_text = str(reply).strip()
                # Remove markdown code blocks if present
                if response_text.startswith("```"):
                    lines = response_text.split("\n")
                    response_text = "\n".join(lines[1:-1]) if len(lines) > 2 else response_text
                    response_text = response_text.strip()

                logger.info(
                    f"DebugAgent {self.participant.name} generated response: {response_text[:100]}..."
                )

                return response_text

        except Exception as e:
            logger.error(
                f"Error generating text response for {self.participant.name}: {e}",
                exc_info=True,
            )

        return None

    def _choose_touchscreen_response(self, prompt: TouchscreenPrompt) -> str | None:
        """Choose a response for a touchscreen prompt (happy path logic)"""
        if not prompt.options:
            return None

        return random.choice(prompt.options)

    async def _submit_response(
        self,
        text_response: str | None,
        touchscreen_response: str | None,
    ):
        """Submit a response to the input queue"""
        if not text_response and not touchscreen_response:
            return

        message = ParticipantInput(type="participant_input")

        # Add spoken response if present
        if text_response:
            message.spoken = SpokenInput(
                participant_id=self.participant.name,
                text=text_response,
                utterance_id=self._next_participant_utterance_id(),
            )

            # If this participant has a raised hand, lower it
            if self.participant.name in self.debate_state.inputs.raised_hands:
                raised_hands = [name for name in self.debate_state.inputs.raised_hands if name != self.participant.name]
                message.raised_hands = raised_hands

        # Add touchscreen response if present
        if touchscreen_response:
            message.touchscreen_responses = {
                self.participant.name: touchscreen_response
            }

        try:
            await self.input_queue.put(message)
            logger.info(f"DebugAgent {self.participant.name} submitted response")
        except Exception as e:
            logger.error(
                f"Error submitting response for {self.participant.name}: {e}",
                exc_info=True,
            )
