"""
OpenCV-based gesture and pose recognition module for Wall-E robot.

This module provides gesture recognition capabilities with minimal coupling
to the existing streaming infrastructure.
"""

from .action_enum import ActionEnum, RecognitionResult
from .gesture_recognizer import GestureRecognizer
from .debouncer import Debouncer
from .pipelines.gesture_pipeline import GesturePipeline

__all__ = [
    "ActionEnum",
    "RecognitionResult",
    "GestureRecognizer",
    "Debouncer",
    "GesturePipeline",
]
