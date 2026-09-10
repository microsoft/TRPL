# -*- coding: utf-8 -*-
from typing import Awaitable, Callable
import logging

from api.logs import logging_node_name
from debate.models.state import DebateState
from debate.nodes.base import BaseNode

logger = logging.getLogger(f"lia.{__name__}")


class MiniGraph:
    END = "__END__"

    def __init__(self):
        self.nodes: dict[str, BaseNode] = {}
        self.entry: str | None = None
        self.pre_node_hook: Callable[[DebateState], Awaitable[bool]] | None = None

    def add_node(self, node: BaseNode, name: str | None = None) -> "MiniGraph":
        self.nodes[name or node.name] = node
        return self

    def set_entry(self, name: str) -> "MiniGraph":
        self.entry = name
        return self

    def set_pre_node_hook(self, hook: Callable[[], Awaitable[bool]]) -> "MiniGraph":
        self.pre_node_hook = hook
        return self

    async def run(self, state: DebateState):
        node = self.entry
        if not node:
            raise RuntimeError("No entry node set.")

        logger.info(f"[Graph] Starting debate graph execution, entry node: {node}")

        while True:
            if node == self.END:
                logger.info(f"[Graph] Reached END node, terminating graph")
                break

            logger.info(f"[Graph] Executing node: {node}")

            node_instance = self.nodes.get(node)
            if node_instance is None:
                logger.error(f"[Graph] Node not found: {node}")
                break

            if self.pre_node_hook:
                if not await self.pre_node_hook():
                    logger.info(
                        f"[Graph] Pre-node hook returned False, terminating graph"
                    )
                    break

            with logging_node_name(node):
                next_node = await node_instance.run(state)

            if not next_node:
                logger.info(f"[Graph] Node {node} returned None, transitioning to END")
                node = self.END
            elif next_node == self.END:
                logger.info(f"[Graph] Node {node} returned END, terminating graph")
                break
            else:
                logger.info(f"[Graph] Node {node} -> {next_node}")
                node = next_node
