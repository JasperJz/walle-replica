"""
Camera implementations for gesture recognition.

Provides abstract camera interface and concrete implementations
for different environments (Mac, Raspberry Pi).
"""

from .base_camera import BaseCamera
from .mock_camera import MockCamera

__all__ = ["BaseCamera", "MockCamera"]
