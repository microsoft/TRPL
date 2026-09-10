# -*- coding: utf-8 -*-
import logging
import asyncio

from debate.debug_agents import DebugAgent
from debate.services.session_store import DebateSession

logger = logging.getLogger(f"lia.{__name__}")


class DebugAgentManager:
    """Manages debug agents that simulate human participants"""

    def __init__(
        self,
        session: DebateSession,
        llm_config: dict,
        participant_agent_config: dict[str, str | None],
    ):
        self.session = session
        self.llm_config = llm_config
        self.participant_agent_config = participant_agent_config
        self.agents: dict[str, DebugAgent] = {}
        self.agent_queues: dict[str, asyncio.Queue] = {}
        self.running = False

    async def start(self):
        """Start the debug agent manager and all agents"""

        if self.running:
            logger.warning(f"DebugAgentManager already running")
            return

        self.running = True

        # Create an agent only for participants with agent_type set
        for name, participant in self.session.state.roster.items():
            agent_type = self.participant_agent_config.get(name)

            # Skip participants without agent_type (they are human)
            if agent_type is None:
                continue

            # Each agent subscribes directly to session.output_queue and gets its own subscription queue
            agent_queue = await self.session.output_queue.subscribe()

            agent = DebugAgent(
                participant=participant,
                debate_state=self.session.state,
                input_queue=self.session.input_queue,
                llm_config=self.llm_config,
                session_id=self.session.session_id,
                agent_type=agent_type,
            )
            self.agents[name] = agent
            self.agent_queues[name] = agent_queue

            # Start agent with its own subscription queue
            await agent.start(agent_queue)

        logger.info(f"DebugAgentManager started with {len(self.agents)} agents")

    async def stop(self):
        """Stop all agents and clean up"""
        self.running = False

        # Stop all agents
        for name, agent in self.agents.items():
            await agent.stop()
            queue = self.agent_queues.get(name)
            if queue is not None:
                try:
                    await self.session.output_queue.unsubscribe(queue)
                except Exception as e:
                    logger.warning(
                        "Failed to unsubscribe debug agent queue for %s: %s",
                        name,
                        e,
                        exc_info=True,
                    )

        self.agent_queues.clear()
        self.agents.clear()

        logger.info(f"DebugAgentManager stopped")
