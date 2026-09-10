from abc import ABC, abstractmethod
import asyncio

from api.config import config
from debate.models.constants import Phase
from debate.models.inputs import HandRaiseInput, SpokenTextInput, TimeoutInput
from debate.models.sockets import HandRaisePrompt
from debate.models.state import DebateState
from debate.nodes.engagement.timeout_nudge import send_timeout_nudge
from debate.services.agent_registry import AgentRegistry
from debate.services.io import IOService
import logging

logger = logging.getLogger(f"lia.{__name__}")


class BaseNode(ABC):
    """Base class for all debate nodes."""

    def __init__(self, io: IOService, agent_registry: AgentRegistry):
        """Initialize node with I/O service and agent registry."""
        self.io = io
        self.agent_registry = agent_registry

    @property
    def name(self) -> str:
        """Node name derived from class name."""
        return self.__class__.__name__

    @abstractmethod
    async def run(self, state: DebateState) -> str:
        """Execute the node logic and return the next node name."""
        pass

    def log_start(self, state: DebateState) -> None:
        """Log node start with consistent formatting."""
        logger.info(f"[{self.name}] Starting, round={state.current_round}")

    def log_transition(self, next_node: str) -> None:
        """Log node transition."""
        logger.info(f"[{self.name}] Transitioning to {next_node}")

    def log_warning(self, message: str) -> None:
        """Log warning with node name prefix."""
        logger.warning(f"[{self.name}] {message}")

    def log_error(self, message: str, exc_info: bool = False) -> None:
        """Log error with node name prefix."""
        logger.error(f"[{self.name}] {message}", exc_info=exc_info)

    async def send_output(self, text: str = "", state: DebateState | None = None, **kwargs) -> None:
        """Wrapper for output.send_output."""
        # Automatically include phase from state if available and not explicitly provided
        if state and "phase" not in kwargs:
            kwargs["phase"] = state.phase
        await self.io.output.send_output(text=text, **kwargs)

    async def send_debug(self, agent: str, content: str) -> None:
        """Wrapper for output.send_debug with automatic phase from node name."""
        await self.io.output.send_debug(
            agent=agent, content=content, phase=self.name.lower()
        )

    def _choose_raised_hand(
        self, raised_hands: list[str], eligible: list[str]
    ) -> str | None:
        """
        Choose the next eligible raised hand from the list of raised hands.
        
        Args:
            raised_hands: List of participant names who have raised their hands
            eligible: List of participant names who are eligible to speak
            
        Returns:
            The first eligible participant who raised their hand, or None if no match
        """
        return next((name for name in raised_hands if name in eligible), None)

    async def _wait_after_hand_raise_prompt(
        self,
        *,
        state: DebateState,
        phase: str,
        eligible_speakers: list[HandRaisePrompt],
        participants: list[str],
    ):
        await self._wait_for_prompt_delivery_before_timeout(state, phase)

        while True:
            if state.inputs.raised_hands:
                self._reset_hand_raise_retry_count(state, phase)
                return HandRaiseInput(participant_ids=list(state.inputs.raised_hands))

            event = await self.io.input.wait_for_input(
                SpokenTextInput,
                HandRaiseInput,
                TimeoutInput,
            )

            if isinstance(event, SpokenTextInput):
                self._reset_hand_raise_retry_count(state, phase)
                return event

            if isinstance(event, HandRaiseInput):
                if state.inputs.raised_hands:
                    self._reset_hand_raise_retry_count(state, phase)
                    return event
                # Hand-raise state update with no raised hands is not a timeout signal.
                continue

            # Only client-emitted TimeoutInput drives timeout nudges.
            retry_index = self._next_hand_raise_retry_count(state, phase)
            if self._should_send_second_retry(phase) and retry_index == 2:
                retry_stage = "second_retry"
            elif retry_index == 1:
                retry_stage = "first_retry"
            else:
                retry_stage = "later_retry"

            await self._send_hand_raise_follow_up(
                state=state,
                phase=phase,
                eligible_speakers=eligible_speakers,
                participants=participants,
                retry_stage=retry_stage,
                retry_index=retry_index,
            )

    async def _wait_for_prompt_delivery_before_timeout(
        self,
        state: DebateState,
        phase: str,
    ) -> None:
        # Caller awaits send_output()/stream.finish() before this method, so text
        # streaming has been fully emitted to the output queue. If audio playback
        # is active, defer timeout start until the frontend reports audio_idle.
        if not config.tts_enabled:
            return

        output_queue = getattr(self.io.output, "output_queue", None)
        if output_queue is None:
            return
        if not getattr(output_queue, "any_audio_subscribers", False):
            return
        if state.inputs.audio_idle:
            return

        logger.info(
            "[%s] Waiting for audio_idle before starting hand-raise timeout (phase=%s)",
            self.name,
            phase,
        )
        while True:
            if state.inputs.audio_idle or state.inputs.raised_hands:
                return
            await asyncio.sleep(0.05)

    async def _send_hand_raise_follow_up(
        self,
        *,
        state: DebateState,
        phase: str,
        eligible_speakers: list[HandRaisePrompt],
        participants: list[str],
        retry_stage: str,
        retry_index: int,
    ) -> None:
        await send_timeout_nudge(
            io=self.io,
            agent_registry=self.agent_registry,
            state=state,
            phase=phase,
            last_prompt=self._get_last_prompt(state, phase),
            participants=participants,
            eligible_speakers=eligible_speakers,
            retry_stage=retry_stage,
            retry_index=retry_index,
            guidance=(
                "Vary wording compared with recent TR lines. "
                "For stronger retries, you may directly invite one or more participants by name."
            ),
            fallback_text=self._fallback_hand_raise_retry_text(
                phase=phase,
                retry_stage=retry_stage,
                retry_index=retry_index,
            ),
        )

    def _get_last_prompt(self, state: DebateState, phase: str) -> str | None:
        return next(
            (
                entry.text
                for entry in reversed(state.history)
                if entry.phase == phase
                and entry.speaker.startswith("[TR")
            ),
            None,
        )

    def _should_send_second_retry(self, phase: str) -> bool:
        try:
            return Phase(phase) is not Phase.welcome
        except ValueError:
            return True

    def _next_hand_raise_retry_count(self, state: DebateState, phase: str) -> int:
        current = state.hand_raise_retry_counts.get(phase, 0)
        updated = current + 1
        state.hand_raise_retry_counts[phase] = updated
        return updated

    def _reset_hand_raise_retry_count(self, state: DebateState, phase: str) -> None:
        state.hand_raise_retry_counts.pop(phase, None)

    def _fallback_hand_raise_retry_text(
        self,
        *,
        phase: str,
        retry_stage: str,
        retry_index: int,
    ) -> str:
        try:
            is_welcome = Phase(phase) is Phase.welcome
        except ValueError:
            is_welcome = False

        if is_welcome:
            options = [
                "Don't be bashful now. Ask me anything and we'll take it together.",
                "Come on in, folks. A small question is all it takes to begin.",
                "No need to be timid. I am eager to hear your thoughts.",
            ]
            return options[(retry_index - 1) % len(options)]

        if retry_stage == "second_retry":
            options = [
                "My advisors, I need your judgment. Who will speak first?",
                "Leadership demands voices in this room. Step forward and be heard.",
                "I cannot weigh this matter alone. Who has counsel for me?",
            ]
            return options[(retry_index - 1) % len(options)]

        options = [
            "I am ready for your counsel. Who wishes to weigh in?",
            "Give me your view so we can move this forward together.",
            "We need your judgment at the table. Who will begin?",
        ]
        return options[(retry_index - 1) % len(options)]
