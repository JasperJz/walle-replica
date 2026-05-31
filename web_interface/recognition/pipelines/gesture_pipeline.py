"""
Main gesture recognition pipeline.

Orchestrates the flow from camera input through recognition to output.
"""

import logging
from typing import Optional, Callable, List
from threading import Thread, Event
import time

from ..cameras.base_camera import BaseCamera
from ..cameras.mock_camera import MockCamera
from ..gesture_recognizer import GestureRecognizer
from ..debouncer import Debouncer
from ..action_enum import ActionEnum, RecognitionResult

logger = logging.getLogger(__name__)


class RecognitionTimeout:
    """Track when to clear old recognition results."""
    def __init__(self, action: ActionEnum, confidence: float, cooldown: float):
        self.action = action
        self.confidence = confidence
        self.timestamp = time.time()
        self.cooldown = cooldown
    
    def is_expired(self) -> bool:
        """Check if this recognition has expired (should be cleared)."""
        return (time.time() - self.timestamp) > self.cooldown


class GesturePipeline:
    """
    Complete gesture recognition pipeline.
    
    Manages camera input, frame processing, gesture recognition,
    and debouncing of results.
    """
    
    def __init__(
        self,
        camera: BaseCamera,
        confidence_threshold: float = 0.5,
        debounce_cooldown: float = 1.0,
    ):
        """
        Initialize the gesture pipeline.
        
        Args:
            camera: Camera implementation to use
            confidence_threshold: Minimum confidence for gesture recognition
            debounce_cooldown: Cooldown period for debouncing (seconds)
        """
        self.camera = camera
        self.recognizer = GestureRecognizer(confidence_threshold)
        self.debouncer = Debouncer(default_cooldown=debounce_cooldown)
        self.debounce_cooldown = debounce_cooldown
        
        self._running = False
        self._thread: Optional[Thread] = None
        self._stop_event = Event()
        self._callbacks: List[Callable[[RecognitionResult], None]] = []
        
        self.frame_count = 0
        self.recognition_count = 0
        self.last_recognized_action = None
        self.last_recognized_confidence = 0.0
        self._last_recognition_timeout: Optional[RecognitionTimeout] = None
    
    def register_callback(self, callback: Callable[[RecognitionResult], None]) -> None:
        """
        Register a callback for recognized gestures.
        
        Args:
            callback: Function to call when gesture is recognized
        """
        self._callbacks.append(callback)
        logger.info(f"Registered callback: {callback.__name__}")
    
    def start(self) -> bool:
        """
        Start the gesture recognition pipeline.
        
        Returns:
            True if started successfully, False otherwise
        """
        if self._running:
            logger.warning("Pipeline already running")
            return False
        
        # Connect to camera
        if not self.camera.connect():
            logger.error("Failed to connect to camera")
            return False
        
        # Start processing thread
        self._stop_event.clear()
        self._running = True
        self._thread = Thread(target=self._process_loop, daemon=False)
        self._thread.start()
        
        logger.info("Gesture pipeline started")
        return True
    
    def stop(self) -> bool:
        """
        Stop the gesture recognition pipeline.
        
        Returns:
            True if stopped successfully, False otherwise
        """
        if not self._running:
            logger.warning("Pipeline not running")
            return False
        
        self._stop_event.set()
        self._running = False
        
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        
        # Disconnect camera
        self.camera.disconnect()
        
        logger.info("Gesture pipeline stopped")
        return True
    
    def is_running(self) -> bool:
        """Check if pipeline is running."""
        return self._running and self._thread is not None and self._thread.is_alive()
    
    def get_stats(self) -> dict:
        """Get pipeline statistics."""
        return {
            "running": self.is_running(),
            "frames_processed": self.frame_count,
            "gestures_recognized": self.recognition_count,
            "last_action": self.last_recognized_action.value if self.last_recognized_action else "None",
            "last_confidence": self.last_recognized_confidence,
            "avg_recognition_rate": (
                self.recognition_count / self.frame_count 
                if self.frame_count > 0 else 0.0
            ),
            "camera_fps": self.camera.get_fps(),
            "camera_resolution": self.camera.get_resolution(),
        }
    
    def _process_loop(self) -> None:
        """Main processing loop - runs in separate thread."""
        try:
            logger.info("Processing loop started")
            
            while not self._stop_event.is_set():
                try:
                    # Check if last recognition has expired (clear it after cooldown)
                    if self._last_recognition_timeout is not None:
                        if self._last_recognition_timeout.is_expired():
                            logger.debug(f"Recognition timeout: clearing {self.last_recognized_action}")
                            self.last_recognized_action = None
                            self.last_recognized_confidence = 0.0
                            self._last_recognition_timeout = None
                    
                    # Capture frame
                    frame = self.camera.get_frame()
                    if frame is None:
                        time.sleep(0.02)
                        continue
                    
                    self.frame_count += 1
                    
                    try:
                        # Process frame through recognizer
                        result = self.recognizer.process_frame(frame)
                        
                        if result is not None:
                            # Check debouncer
                            if self.debouncer.should_process(result):
                                self.recognition_count += 1
                                self.last_recognized_action = result.action
                                self.last_recognized_confidence = result.confidence
                                # Create timeout for this recognition
                                self._last_recognition_timeout = RecognitionTimeout(
                                    result.action, 
                                    result.confidence,
                                    self.debounce_cooldown
                                )
                                
                                # Call registered callbacks
                                for callback in self._callbacks:
                                    try:
                                        callback(result)
                                    except Exception as e:
                                        logger.error(f"Error in callback {callback.__name__}: {e}")
                                
                                logger.debug(f"Gesture recognized: {result}")
                    except Exception as e:
                        logger.error(f"Error during recognition: {e}")
                        time.sleep(0.02)
                
                except Exception as e:
                    logger.error(f"Error getting frame: {e}")
                    time.sleep(0.02)
        
        except Exception as e:
            logger.error(f"Error in processing loop: {e}")
        finally:
            self._running = False
            logger.info(f"Processing loop ended. Processed {self.frame_count} frames, "
                       f"recognized {self.recognition_count} gestures")
    
    def reset_stats(self) -> None:
        """Reset pipeline statistics."""
        self.frame_count = 0
        self.recognition_count = 0
        self.recognizer.reset()
        self.debouncer.reset()
