"""
Abstract base class for camera implementations.

Defines the interface that all camera implementations must follow.
"""

from abc import ABC, abstractmethod
from typing import Optional, Tuple
import numpy as np


class BaseCamera(ABC):
    """
    Abstract base class for camera implementations.
    
    Subclasses must implement methods to capture frames from
    different camera sources (mock, Pi camera, etc.).
    """
    
    @abstractmethod
    def connect(self) -> bool:
        """
        Connect to the camera.
        
        Returns:
            True if connection successful, False otherwise
        """
        pass
    
    @abstractmethod
    def disconnect(self) -> bool:
        """
        Disconnect from the camera.
        
        Returns:
            True if disconnection successful, False otherwise
        """
        pass
    
    @abstractmethod
    def is_connected(self) -> bool:
        """
        Check if camera is connected.
        
        Returns:
            True if connected, False otherwise
        """
        pass
    
    @abstractmethod
    def get_frame(self) -> Optional[np.ndarray]:
        """
        Get the next frame from the camera.
        
        Returns:
            Frame as numpy array in BGR format (OpenCV format),
            or None if frame is unavailable
        """
        pass
    
    @abstractmethod
    def get_resolution(self) -> Tuple[int, int]:
        """
        Get camera resolution (width, height).
        
        Returns:
            Tuple of (width, height)
        """
        pass
    
    @abstractmethod
    def get_fps(self) -> float:
        """
        Get camera frames per second.
        
        Returns:
            FPS as float
        """
        pass
    
    @abstractmethod
    def set_resolution(self, width: int, height: int) -> bool:
        """
        Set camera resolution.
        
        Args:
            width: Desired frame width
            height: Desired frame height
            
        Returns:
            True if successful, False otherwise
        """
        pass
