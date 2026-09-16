# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
LLM agent module
For analyzing room monitoring data and generating intelligent reports
"""
import json
import os
import time
from typing import Dict, Optional, List
import logging

from ..utils.prompts import (
    SYSTEM_ROOM_ANALYSIS, PROMPT_ROOM_ANALYSIS,
    SYSTEM_EVENT_SUMMARY, PROMPT_EVENT_SUMMARY,
    SYSTEM_QA, PROMPT_QA,
)

logger = logging.getLogger(__name__)


class LLMAgent:
    """LLM Agent - Analyzes room monitoring data and generates reports"""

    def __init__(self, model: str = "gpt-4", temperature: float = 0.7,
                 max_tokens: int = 2000, timeout: float = 30):
        """
        Args:
            model: Model name ("gpt-4", "gpt-3.5-turbo", etc.)
            temperature: Temperature (creativity level)
            max_tokens: Maximum output tokens
            timeout: Request timeout
        """
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.api_key = os.getenv("OPENAI_API_KEY")

        if not self.api_key:
            logger.warning("OPENAI_API_KEY not set; LLM features will be limited")

        self.client = self._init_client()

    def _init_client(self):
        """Initialize OpenAI client"""
        try:
            from openai import OpenAI
            if self.api_key:
                return OpenAI(api_key=self.api_key)
            else:
                return None
        except ImportError:
            logger.error("openai library not installed; install with: pip install openai")
            return None

    def analyze_room_state(self, room_summary: Dict) -> Optional[str]:
        """
        Analyze the current room state

        Args:
            room_summary: Data from EventManager.get_room_summary()

        Returns:
            LLM analysis result, or None on failure
        """
        if not self.client:
            logger.warning("LLM client not available")
            return None

        summary_json = json.dumps(room_summary, ensure_ascii=False, indent=2)
        prompt = PROMPT_ROOM_ANALYSIS.format(summary_json=summary_json)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_ROOM_ANALYSIS},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=self.timeout,
            )

            result = response.choices[0].message.content
            logger.info(f"LLM analysis completed: {len(result)} chars")
            return result

        except Exception as e:
            logger.error(f"Error calling LLM: {e}")
            return None

    def generate_event_summary(self, events: List[Dict]) -> Optional[str]:
        """
        Generate event summary

        Args:
            events: Event list (from EventManager.get_events())

        Returns:
            LLM-generated event summary
        """
        if not self.client:
            return None

        events_json = json.dumps(events, ensure_ascii=False, indent=2)
        prompt = PROMPT_EVENT_SUMMARY.format(events_json=events_json)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_EVENT_SUMMARY},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=self.timeout,
            )

            result = response.choices[0].message.content
            logger.info(f"Event summary generated: {len(result)} chars")
            return result

        except Exception as e:
            logger.error(f"Error generating event summary: {e}")
            return None

    def answer_question(self, question: str, context: Dict) -> Optional[str]:
        if not self.client:
            return None

        context_json = json.dumps(context, ensure_ascii=False, indent=2)
        prompt = PROMPT_QA.format(context_json=context_json, question=question)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_QA},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=self.timeout,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Error answering question: {e}")
            return None
