# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Pydantic models for event payloads received from the camera system.
"""
from typing import Any, Dict, Optional
from pydantic import BaseModel


class CameraEvent(BaseModel):
    """
    Generic event payload sent by the camera platform.

    event_type values:
      - person_entered   : new person detected entering the room
      - person_left      : person left the room (timeout)
      - hand_raised      : person raised a hand
      - hand_lowered     : person lowered their hand
      - person_moved     : position update
      - pose_changed     : general pose change
    """
    event_type: str
    timestamp: float
    camera_id: str
    person_id: int
    data: Dict[str, Any] = {}


class PersonDescription(BaseModel):
    """VLM-generated description of a person."""
    person_id: int
    camera_id: str
    timestamp: float
    description: str
    bbox: Optional[list] = None
