"""
Mock camera implementation using OpenCV VideoCapture.

Allows testing the recognition pipeline on Mac or Linux with
built-in webcam or video files.
"""

import logging
from typing import Optional, Tuple
import numpy as np
import cv2

logger = logging.getLogger(__name__)


class MockCamera:
    """
    Mock camera that captures from system webcam or video file.
    
    Useful for local development on Mac/Linux without Raspberry Pi camera.
    """
    
    def __init__(self, camera_index: int = 0, width: int = 640, height: int = 480):
        """
        Initialize mock camera.
        
        Args:
            camera_index: Camera index (0 for default webcam)
            width: Desired frame width
            height: Desired frame height
        """
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.cap: Optional[cv2.VideoCapture] = None
        self._connected = False
        self._fps = 30.0
    
    def connect(self) -> bool:
        """
        Connect to the camera.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.cap = cv2.VideoCapture(self.camera_index)
            
            if not self.cap.isOpened():
                logger.error(f"Failed to open camera {self.camera_index}")
                return False
            
            # Set resolution
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            
            # Set FPS (optional, may not work on all cameras)
            self.cap.set(cv2.CAP_PROP_FPS, 30)
            
            # Get FPS
            self._fps = self.cap.get(cv2.CAP_PROP_FPS)
            if self._fps <= 0:
                self._fps = 30.0
            
            # Try to read a few frames to warm up the camera
            # Some cameras need a few frames to initialize properly
            for _ in range(3):
                ret, frame = self.cap.read()
                if ret:
                    break
            
            # If we got at least one frame, consider it a success
            if frame is not None:
                self._connected = True
                logger.info(f"MockCamera connected: {self.width}x{self.height} @ {self._fps:.1f}fps")
                return True
            else:
                logger.error("Failed to read initial frames from camera")
                self.cap.release()
                self.cap = None
                return False
            
        except Exception as e:
            logger.error(f"Error connecting to mock camera: {e}")
            if self.cap is not None:
                self.cap.release()
                self.cap = None
            return False
    
    def disconnect(self) -> bool:
        """
        Disconnect from the camera.
        
        Returns:
            True if disconnection successful, False otherwise
        """
        try:
            if self.cap is not None:
                self.cap.release()
                self.cap = None
            self._connected = False
            logger.info("MockCamera disconnected")
            return True
        except Exception as e:
            logger.error(f"Error disconnecting mock camera: {e}")
            return False
    
    def is_connected(self) -> bool:
        """
        Check if camera is connected.
        
        Returns:
            True if connected, False otherwise
        """
        return self._connected and self.cap is not None and self.cap.isOpened()
    
    def get_frame(self) -> Optional[np.ndarray]:
        """
        Get the next frame from the camera.
        
        Returns:
            Frame as numpy array in BGR format, or None if unavailable
        """
        if not self.is_connected():
            return None
        
        try:
            ret, frame = self.cap.read()
            if ret:
                return frame
            return None
        except Exception as e:
            logger.error(f"Error reading frame: {e}")
            return None
    
    def get_resolution(self) -> Tuple[int, int]:
        """
        Get camera resolution (width, height).
        
        Returns:
            Tuple of (width, height)
        """
        if self.cap is None:
            return (self.width, self.height)
        
        width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return (width, height)
    
    def get_fps(self) -> float:
        """
        Get camera frames per second.
        
        Returns:
            FPS as float
        """
        return self._fps
    
    def set_resolution(self, width: int, height: int) -> bool:
        """
        Set camera resolution.
        
        Args:
            width: Desired frame width
            height: Desired frame height
            
        Returns:
            True if successful, False otherwise
        """
        if not self.is_connected():
            return False
        
        try:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            
            actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            
            self.width = actual_width
            self.height = actual_height
            
            logger.info(f"MockCamera resolution set to {actual_width}x{actual_height}")
            return True
            
        except Exception as e:
            logger.error(f"Error setting resolution: {e}")
            return False
    
    @staticmethod
    def list_available_cameras() -> list:
        """
        List available cameras on the system.
        
        Returns:
            List of available camera indices
        """
        available = []
        for i in range(10):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                available.append(i)
                cap.release()
        return available
