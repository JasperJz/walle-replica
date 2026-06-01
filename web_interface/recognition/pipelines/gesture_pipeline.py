"""
Main gesture recognition pipeline.

Orchestrates the flow from camera input through recognition to output.
"""

import logging
from typing import Optional, Callable, List
from threading import Thread, Event
from collections import defaultdict, deque
import time

from ..cameras.base_camera import BaseCamera
from ..cameras.mock_camera import MockCamera
from ..gesture_recognizer import GestureRecognizer
from ..debouncer import Debouncer
from ..action_enum import ActionEnum, RecognitionResult

logger = logging.getLogger(__name__)


class VotingWindow:
    """
    Voting window for gesture recognition stability.
    
    Collects gesture recognition results over a sliding window of frames
    and only triggers recognition when a gesture is voted for sufficiently
    across multiple frames. This improves stability and reduces false positives.
    """
    
    def __init__(self, window_size: int = 30, vote_threshold: float = 0.5):
        """
        Initialize voting window.
        
        Args:
            window_size: Number of frames to consider (default 30 for ~1 second at 30fps)
            vote_threshold: Proportion of frames needed to trigger recognition (0.0-1.0)
        """
        self.window_size = window_size
        self.vote_threshold = vote_threshold
        self.frame_votes: deque = deque(maxlen=window_size)
        self.last_voted_action = None
    
    def add_vote(self, result: Optional[RecognitionResult]) -> None:
        """
        Add a recognition result to the voting window.
        
        Args:
            result: Recognition result or None if no gesture detected
        """
        if result is not None:
            self.frame_votes.append(result.action)
        else:
            self.frame_votes.append(None)
    
    def get_voted_action(self) -> Optional[ActionEnum]:
        """
        Get the action with the most votes in current window.
        
        Returns:
            The action if it exceeds vote threshold, None otherwise
        """
        if len(self.frame_votes) == 0:
            return None
        
        # If window not full yet, be more lenient
        current_window_size = len(self.frame_votes)
        required_votes = max(1, int(current_window_size * self.vote_threshold))
        
        # Count votes for each action
        vote_counts = defaultdict(int)
        
        for vote in self.frame_votes:
            if vote is not None:
                vote_counts[vote] += 1
        
        if not vote_counts:
            return None
        
        # Find action with most votes
        max_action = max(vote_counts.items(), key=lambda x: x[1])
        action, count = max_action
        
        # Check if it exceeds threshold based on non-None votes
        if count >= required_votes:
            # Only return if it's consistent (same action twice in window suggests stability)
            if current_window_size >= 2:
                return action
            elif count >= 1 and current_window_size == 1:
                # Very first frame detected, accept it
                return action
        
        return None
    
    def reset(self) -> None:
        """Clear the voting window."""
        self.frame_votes.clear()



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
        voting_window_size: int = 30,
        voting_threshold: float = 0.5,
        idle_timeout: float = 3.0,
    ):
        """
        Initialize the gesture pipeline.
        
        Args:
            camera: Camera implementation to use
            confidence_threshold: Minimum confidence for gesture recognition
            debounce_cooldown: Cooldown period for debouncing (seconds)
            voting_window_size: Number of frames for voting window (default 30 for ~1sec at 30fps)
            voting_threshold: Proportion of frames needed to trigger (0.0-1.0, default 0.5 = 50%)
            idle_timeout: Seconds of no detection before returning to IDLE (default 3.0)
        """
        self.camera = camera
        self.recognizer = GestureRecognizer(confidence_threshold)
        self.debouncer = Debouncer(default_cooldown=debounce_cooldown)
        self.debounce_cooldown = debounce_cooldown
        self.voting_window = VotingWindow(
            window_size=voting_window_size,
            vote_threshold=voting_threshold
        )
        self.idle_timeout = idle_timeout
        
        self._running = False
        self._thread: Optional[Thread] = None
        self._stop_event = Event()
        self._callbacks: List[Callable[[RecognitionResult], None]] = []
        
        self.frame_count = 0
        self.recognition_count = 0
        self.last_recognized_action = None
        self.last_recognized_confidence = 0.0
        self.last_recognition_time = time.time()  # Track time of last recognition for idle timeout
        self.start_time = time.time()  # Track pipeline start time for rate calculation
    
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
    
    def set_idle_timeout(self, timeout: float) -> None:
        """
        Set the idle timeout duration.
        
        Args:
            timeout: Seconds of no recognition before returning to IDLE
        """
        self.idle_timeout = timeout
        logger.info(f"Idle timeout set to {timeout} seconds")
    
    def get_idle_timeout(self) -> float:
        """Get the current idle timeout duration."""
        return self.idle_timeout
    
    def get_stats(self) -> dict:
        """Get pipeline statistics."""
        elapsed_time = time.time() - self.start_time
        time_since_recognition = time.time() - self.last_recognition_time if self.last_recognized_action else 0
        
        return {
            "running": self.is_running(),
            "frames_processed": self.frame_count,
            "gestures_recognized": self.recognition_count,
            "last_action": self.last_recognized_action.value if self.last_recognized_action else "idle",
            "last_confidence": self.last_recognized_confidence,
            "time_since_recognition": time_since_recognition,
            "idle_timeout": self.idle_timeout,
            "recognition_rate": (
                self.recognition_count / elapsed_time 
                if elapsed_time > 0 else 0.0
            ),
            "elapsed_time": elapsed_time,
            "camera_fps": self.camera.get_fps(),
            "camera_resolution": self.camera.get_resolution(),
        }
    
    def _process_loop(self) -> None:
        """Main processing loop - runs in separate thread."""
        try:
            logger.info("Processing loop started")
            
            while not self._stop_event.is_set():
                try:
                    current_time = time.time()
                    
                    # Check for idle timeout: if no new recognition for idle_timeout seconds
                    if self.last_recognized_action is not None:
                        time_since_last_recognition = current_time - self.last_recognition_time
                        if time_since_last_recognition > self.idle_timeout:
                            logger.info(f"⏱️ Idle timeout ({self.idle_timeout}s): {self.last_recognized_action.value} → IDLE")
                            self.last_recognized_action = None
                            self.last_recognized_confidence = 0.0
                    
                    # Capture frame
                    frame = self.camera.get_frame()
                    if frame is None:
                        time.sleep(0.02)
                        continue
                    
                    self.frame_count += 1
                    
                    try:
                        # Process frame through recognizer
                        result = self.recognizer.process_frame(frame)
                        
                        # Add to voting window
                        self.voting_window.add_vote(result)
                        
                        # Check if voting window has decided
                        voted_action = self.voting_window.get_voted_action()
                        
                        if voted_action is not None:
                            # Create a result from the voted action
                            voted_result = RecognitionResult(
                                action=voted_action,
                                confidence=0.7,  # Use consistent confidence for voted results
                                timestamp=time.time(),
                                frame_number=self.frame_count,
                            )
                            
                            # Check debouncer only to prevent rapid re-triggering of same action
                            if self.debouncer.should_process(voted_result):
                                self.recognition_count += 1
                                self.last_recognized_action = voted_result.action
                                self.last_recognized_confidence = voted_result.confidence
                                self.last_recognition_time = current_time  # Update recognition timestamp
                                
                                # Call registered callbacks
                                for callback in self._callbacks:
                                    try:
                                        callback(voted_result)
                                    except Exception as e:
                                        logger.error(f"Error in callback {callback.__name__}: {e}")
                                
                                logger.info(f"✓ Gesture recognized: {voted_result.action.value}")
                                
                                # Reset voting window after successful recognition
                                self.voting_window.reset()
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
        self.start_time = time.time()
        self.recognizer.reset()
        self.debouncer.reset()
        self.voting_window.reset()
