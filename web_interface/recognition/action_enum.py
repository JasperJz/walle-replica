"""
Action enumeration and recognition result types.

Defines all possible robot actions that can be recognized from video input.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional


class ActionEnum(str, Enum):
    """Enumeration of robot actions that can be recognized from gestures."""
    
    WAVE = "wave"
    TOUCH = "touch"
    IDLE = "idle"
    WAVE_LEFT = "wave_left"
    WAVE_RIGHT = "wave_right"
    POINT = "point"
    THUMBS_UP = "thumbs_up"
    CLAP = "clap"


@dataclass
class RecognitionResult:
    """Result of a gesture recognition operation."""
    
    action: ActionEnum
    confidence: float  # 0.0 to 1.0
    timestamp: float
    frame_number: Optional[int] = None
    metadata: Optional[dict] = None  # For additional data (e.g., pose landmarks)
    
    def __repr__(self) -> str:
        return f"RecognitionResult(action={self.action.value}, confidence={self.confidence:.2f})"
