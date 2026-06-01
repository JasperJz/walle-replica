"""
Main gesture recognition pipeline.

Orchestrates the flow from camera input through recognition to output.
"""

import logging
import math
import time
from collections import defaultdict, deque
from threading import Event, Thread
from typing import Callable, List, Optional

from ..action_enum import RecognitionResult
from ..cameras.base_camera import BaseCamera
from ..debouncer import Debouncer
from ..gesture_recognizer import GestureRecognizer

logger = logging.getLogger(__name__)


class ConfirmationWindow:
    """Small confirmation buffer used only after a trigger has fired."""

    def __init__(self, window_size: int = 5, min_votes: int = 3):
        self.window_size = window_size
        self.min_votes = min_votes
        self.frame_votes: deque = deque(maxlen=window_size)

    def add_result(self, result: Optional[RecognitionResult]) -> None:
        """Add a classification result to the confirmation buffer."""
        self.frame_votes.append(result)

    def get_confirmed_result(self) -> Optional[RecognitionResult]:
        """Return the dominant action once enough votes have accumulated."""
        if len(self.frame_votes) < self.min_votes:
            return None

        vote_counts = defaultdict(int)
        confidence_sums = defaultdict(float)

        for result in self.frame_votes:
            if result is None:
                continue
            vote_counts[result.action] += 1
            confidence_sums[result.action] += result.confidence

        if not vote_counts:
            return None

        action, count = max(vote_counts.items(), key=lambda item: item[1])
        if count < self.min_votes:
            return None

        average_confidence = confidence_sums[action] / count
        vote_ratio = count / len(self.frame_votes)
        return RecognitionResult(
            action=action,
            confidence=vote_ratio,
            timestamp=time.time(),
            metadata={
                "vote_ratio": vote_ratio,
                "classifier_confidence": average_confidence,
            },
        )

    def reset(self) -> None:
        """Clear confirmation history."""
        self.frame_votes.clear()


class GesturePipeline:
    """
    Complete gesture recognition pipeline.

    The pipeline runs in three stages:
    - idle: only run light trigger detection
    - confirming: run full recognition for a short confirmation burst
    - holding: keep the last result stable until trigger activity disappears
    """

    def __init__(
        self,
        camera: BaseCamera,
        confidence_threshold: float = 0.5,
        debounce_cooldown: float = 1.0,
        voting_window_size: int = 5,
        voting_threshold: float = 0.6,
        idle_timeout: float = 1.0,
        gesture_model_path: Optional[str] = None,
    ):
        """
        Initialize the gesture pipeline.

        Args:
            camera: Camera implementation to use
            confidence_threshold: Minimum confidence for gesture recognition
            debounce_cooldown: Cooldown period for debouncing (seconds)
            voting_window_size: Number of frames used during confirmation
            voting_threshold: Proportion of confirmation frames needed to trigger
            idle_timeout: Maximum time to keep a latched gesture active
            gesture_model_path: Optional path to a MediaPipe gesture recognizer task model
        """
        self.camera = camera
        self.recognizer = GestureRecognizer(
            confidence_threshold=confidence_threshold,
            gesture_model_path=gesture_model_path,
        )
        self.debouncer = Debouncer(default_cooldown=debounce_cooldown)
        self.debounce_cooldown = debounce_cooldown

        confirmation_min_votes = max(2, math.ceil(voting_window_size * voting_threshold))
        self.confirmation_window = ConfirmationWindow(
            window_size=voting_window_size,
            min_votes=confirmation_min_votes,
        )

        self.idle_timeout = idle_timeout
        self.confirm_timeout = 0.75
        self.confirm_cancel_frames = 4
        self.release_consecutive_frames = 4
        self.reconfirm_delay = max(0.4, debounce_cooldown)

        self._running = False
        self._thread: Optional[Thread] = None
        self._stop_event = Event()
        self._callbacks: List[Callable[[RecognitionResult], None]] = []

        self._state = "idle"
        self._confirm_started_at = 0.0
        self._confirm_miss_frames = 0
        self._hold_miss_frames = 0
        self._last_trigger_score = 0.0

        self.frame_count = 0
        self.recognition_count = 0
        self.last_recognized_action = None
        self.last_recognized_confidence = 0.0
        self.last_recognition_time = 0.0
        self.start_time = time.time()

    def register_callback(self, callback: Callable[[RecognitionResult], None]) -> None:
        """Register a callback for recognized gestures."""
        self._callbacks.append(callback)
        logger.info(f"Registered callback: {callback.__name__}")

    def start(self) -> bool:
        """Start the gesture recognition pipeline."""
        if self._running:
            logger.warning("Pipeline already running")
            return False

        if not self.camera.connect():
            logger.error("Failed to connect to camera")
            return False

        self._stop_event.clear()
        self._running = True
        self._thread = Thread(target=self._process_loop, daemon=False)
        self._thread.start()

        logger.info("Gesture pipeline started")
        return True

    def stop(self) -> bool:
        """Stop the gesture recognition pipeline."""
        if not self._running:
            logger.warning("Pipeline not running")
            return False

        self._stop_event.set()
        self._running = False

        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

        self.camera.disconnect()
        logger.info("Gesture pipeline stopped")
        return True

    def is_running(self) -> bool:
        """Check if pipeline is running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    def set_idle_timeout(self, timeout: float) -> None:
        """Set the idle timeout duration."""
        self.idle_timeout = timeout
        logger.info(f"Idle timeout set to {timeout} seconds")

    def get_idle_timeout(self) -> float:
        """Get the current idle timeout duration."""
        return self.idle_timeout

    def get_stats(self) -> dict:
        """Get pipeline statistics."""
        elapsed_time = time.time() - self.start_time
        time_since_recognition = (
            time.time() - self.last_recognition_time if self.last_recognized_action else 0.0
        )

        return {
            "running": self.is_running(),
            "state": self._state,
            "frames_processed": self.frame_count,
            "gestures_recognized": self.recognition_count,
            "last_action": self.last_recognized_action.value if self.last_recognized_action else "idle",
            "last_confidence": self.last_recognized_confidence,
            "time_since_recognition": time_since_recognition,
            "idle_timeout": self.idle_timeout,
            "trigger_score": self._last_trigger_score,
            "recognition_rate": (
                self.recognition_count / elapsed_time if elapsed_time > 0 else 0.0
            ),
            "elapsed_time": elapsed_time,
            "camera_fps": self.camera.get_fps(),
            "camera_resolution": self.camera.get_resolution(),
        }

    def _enter_confirming(self, current_time: float) -> None:
        """Start a short burst of full recognition after a trigger fires."""
        self._state = "confirming"
        self._confirm_started_at = current_time
        self._confirm_miss_frames = 0
        self.confirmation_window.reset()

    def _enter_holding(self) -> None:
        """Latch the last recognition until activity disappears."""
        self._state = "holding"
        self._hold_miss_frames = 0
        self.confirmation_window.reset()

    def _release_to_idle(self) -> None:
        """Release the current latched action and return to idle."""
        self._state = "idle"
        self._confirm_miss_frames = 0
        self._hold_miss_frames = 0
        self.confirmation_window.reset()
        self.last_recognized_action = None
        self.last_recognized_confidence = 0.0

    def _emit_result(self, result: RecognitionResult, current_time: float) -> None:
        """Publish a confirmed gesture if it passes debouncing."""
        result.timestamp = current_time
        result.frame_number = self.frame_count

        if self.debouncer.should_process(result):
            self.recognition_count += 1
            self.last_recognized_action = result.action
            self.last_recognized_confidence = result.confidence
            self.last_recognition_time = current_time

            for callback in self._callbacks:
                try:
                    callback(result)
                except Exception as exc:
                    logger.error(f"Error in callback {callback.__name__}: {exc}")

            logger.info(f"✓ Gesture recognized: {result.action.value}")

    def _run_confirmation(self, frame, current_time: float, trigger_active: bool) -> None:
        """Run full recognition during the short confirmation window."""
        result = self.recognizer.process_frame(frame)
        self.confirmation_window.add_result(result)

        if trigger_active:
            self._confirm_miss_frames = 0
        else:
            self._confirm_miss_frames += 1

        confirmed_result = self.confirmation_window.get_confirmed_result()
        if confirmed_result is not None:
            self._emit_result(confirmed_result, current_time)
            self._enter_holding()
            return

        confirm_timed_out = (current_time - self._confirm_started_at) >= self.confirm_timeout
        if self._confirm_miss_frames >= self.confirm_cancel_frames or confirm_timed_out:
            self._release_to_idle()

    def _run_holding(self, current_time: float, trigger_active: bool) -> None:
        """Keep output stable until gesture activity has clearly ended."""
        if trigger_active:
            self._hold_miss_frames = 0
            if (current_time - self.last_recognition_time) >= self.reconfirm_delay:
                self._enter_confirming(current_time)
            return

        self._hold_miss_frames += 1
        hold_expired = (current_time - self.last_recognition_time) >= self.idle_timeout
        if self._hold_miss_frames >= self.release_consecutive_frames or hold_expired:
            self._release_to_idle()

    def _process_loop(self) -> None:
        """Main processing loop - runs in separate thread."""
        try:
            logger.info("Processing loop started")

            while not self._stop_event.is_set():
                try:
                    current_time = time.time()
                    frame = self.camera.get_frame()
                    if frame is None:
                        time.sleep(0.02)
                        continue

                    self.frame_count += 1
                    trigger_active, trigger_score = self.recognizer.detect_trigger(frame)
                    self._last_trigger_score = trigger_score

                    if self._state == "idle" and trigger_active:
                        self._enter_confirming(current_time)

                    if self._state == "confirming":
                        self._run_confirmation(frame, current_time, trigger_active)
                    elif self._state == "holding":
                        self._run_holding(current_time, trigger_active)

                except Exception as exc:
                    logger.error(f"Error in processing loop iteration: {exc}")
                    time.sleep(0.02)

        except Exception as exc:
            logger.error(f"Error in processing loop: {exc}")
        finally:
            self._running = False
            logger.info(
                f"Processing loop ended. Processed {self.frame_count} frames, "
                f"recognized {self.recognition_count} gestures"
            )

    def reset_stats(self) -> None:
        """Reset pipeline statistics and internal temporal state."""
        self.frame_count = 0
        self.recognition_count = 0
        self.last_recognized_action = None
        self.last_recognized_confidence = 0.0
        self.last_recognition_time = 0.0
        self.start_time = time.time()
        self._state = "idle"
        self._confirm_started_at = 0.0
        self._confirm_miss_frames = 0
        self._hold_miss_frames = 0
        self._last_trigger_score = 0.0
        self.recognizer.reset()
        self.debouncer.reset()
        self.confirmation_window.reset()
