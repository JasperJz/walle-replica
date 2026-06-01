"""
Gesture and pose recognition using OpenCV and MediaPipe.

This module detects human poses and gestures from video frames.
"""

import logging
import time
from collections import deque
from typing import Optional, Tuple

import cv2
import numpy as np

try:
    import mediapipe as mp

    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False

from .action_enum import ActionEnum, RecognitionResult


logger = logging.getLogger(__name__)


class GestureRecognizer:
    """
    Recognizes human gestures and poses from video frames.

    The recognizer exposes two paths:
    1. `detect_trigger()` for a lighter-weight "something is happening" signal.
    2. `process_frame()` for full gesture classification.
    """

    def __init__(self, confidence_threshold: float = 0.5):
        """
        Initialize the gesture recognizer.

        Args:
            confidence_threshold: Minimum confidence score for gesture recognition
        """
        if not MEDIAPIPE_AVAILABLE:
            logger.warning("MediaPipe not available. Install with: pip install mediapipe")
            self.mp_pose = None
            self.mp_hands = None
            self.pose = None
            self.hands = None
        else:
            self.mp_pose = mp.solutions.pose
            self.mp_hands = mp.solutions.hands
            self.pose = self.mp_pose.Pose(
                static_image_mode=False,
                model_complexity=0,
                smooth_landmarks=True,
                min_detection_confidence=confidence_threshold,
                min_tracking_confidence=confidence_threshold,
            )
            self.hands = self.mp_hands.Hands(
                static_image_mode=False,
                max_num_hands=2,
                min_detection_confidence=confidence_threshold,
                min_tracking_confidence=confidence_threshold,
            )

        self.confidence_threshold = confidence_threshold
        self.frame_count = 0
        self._motion_threshold = 0.028
        self._wrist_motion_threshold = 0.02
        self._prev_motion_frame: Optional[np.ndarray] = None
        self._prev_left_wrist_x: Optional[float] = None
        self._prev_right_wrist_x: Optional[float] = None
        self._wave_history = {
            "left": deque(maxlen=12),
            "right": deque(maxlen=12),
        }

    def detect_trigger(self, frame: np.ndarray) -> Tuple[bool, float]:
        """
        Run a light-weight trigger detector.

        It combines cheap frame-difference motion with pose-only signals
        so the pipeline can avoid full hand+pose recognition on every frame.
        """
        motion_score = self._estimate_frame_motion(frame)
        pose_score = 0.0

        if self.pose is not None:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pose_results = self.pose.process(rgb_frame)
            if pose_results.pose_landmarks:
                pose_score = self._estimate_pose_activity(pose_results.pose_landmarks.landmark)

        trigger_score = max(motion_score, pose_score)
        return trigger_score >= self._motion_threshold, trigger_score

    def process_frame(self, frame: np.ndarray) -> Optional[RecognitionResult]:
        """
        Process a video frame and detect gestures.

        Args:
            frame: Input frame in BGR format (OpenCV format)

        Returns:
            RecognitionResult if gesture detected, None otherwise
        """
        if self.pose is None or self.hands is None:
            logger.warning("Recognition unavailable - MediaPipe not initialized")
            return None

        self.frame_count += 1
        timestamp = time.time()
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        pose_results = self.pose.process(rgb_frame)
        hand_results = self.hands.process(rgb_frame)

        if hand_results.multi_hand_landmarks:
            result = self._recognize_hand_gesture(hand_results, timestamp)
            if result:
                return result

        if pose_results.pose_landmarks:
            result = self._recognize_pose_gesture(pose_results.pose_landmarks.landmark, timestamp)
            if result:
                return result

        return None

    def _estimate_frame_motion(self, frame: np.ndarray) -> float:
        """Estimate coarse upper-body motion using frame differencing."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (160, 120))
        upper_region = small[:80, :]

        if self._prev_motion_frame is None:
            self._prev_motion_frame = upper_region
            return 0.0

        diff = cv2.absdiff(upper_region, self._prev_motion_frame)
        _, thresholded = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        self._prev_motion_frame = upper_region
        return float(np.count_nonzero(thresholded)) / float(thresholded.size)

    def _estimate_pose_activity(self, landmarks) -> float:
        """
        Estimate whether a gesture-like body movement is happening.

        Raised arms and wrist lateral movement are treated as strong triggers.
        """
        left_shoulder = landmarks[11]
        right_shoulder = landmarks[12]
        left_wrist = landmarks[15]
        right_wrist = landmarks[16]

        left_raised = self._is_arm_raised(left_shoulder, left_wrist)
        right_raised = self._is_arm_raised(right_shoulder, right_wrist)
        left_wave_ready = self._is_wave_arm_active(left_shoulder, left_wrist)
        right_wave_ready = self._is_wave_arm_active(right_shoulder, right_wrist)

        left_delta = 0.0
        right_delta = 0.0
        if self._prev_left_wrist_x is not None:
            left_delta = abs(left_wrist.x - self._prev_left_wrist_x)
        if self._prev_right_wrist_x is not None:
            right_delta = abs(right_wrist.x - self._prev_right_wrist_x)

        self._prev_left_wrist_x = left_wrist.x
        self._prev_right_wrist_x = right_wrist.x

        self._update_wave_history("left", left_wrist.x, left_wave_ready)
        self._update_wave_history("right", right_wrist.x, right_wave_ready)

        raised_score = 0.06 if (left_raised or right_raised or left_wave_ready or right_wave_ready) else 0.0
        wrist_motion_score = max(left_delta, right_delta)
        if wrist_motion_score >= self._wrist_motion_threshold:
            wrist_motion_score += 0.02

        return max(raised_score, wrist_motion_score)

    def _recognize_hand_gesture(self, hand_results, timestamp: float) -> Optional[RecognitionResult]:
        """Recognize hand-based gestures."""
        if not hand_results.multi_hand_landmarks:
            return None

        for hand_landmarks in hand_results.multi_hand_landmarks:
            if self._is_thumbs_up(hand_landmarks):
                return RecognitionResult(
                    action=ActionEnum.THUMBS_UP,
                    confidence=0.75,
                    timestamp=timestamp,
                    frame_number=self.frame_count,
                )

        return None

    def _recognize_pose_gesture(self, landmarks, timestamp: float) -> Optional[RecognitionResult]:
        """Recognize body pose-based gestures."""
        if self._is_waving(landmarks):
            return RecognitionResult(
                action=ActionEnum.WAVE,
                confidence=0.8,
                timestamp=timestamp,
                frame_number=self.frame_count,
            )

        return None

    def _is_thumbs_up(self, hand_landmarks) -> bool:
        """Check if hand pose indicates thumbs up gesture."""
        try:
            wrist = hand_landmarks.landmark[0]
            thumb_mcp = hand_landmarks.landmark[2]
            thumb_tip = hand_landmarks.landmark[4]
            thumb_ip = hand_landmarks.landmark[3]
            index_tip = hand_landmarks.landmark[8]
            middle_tip = hand_landmarks.landmark[12]
            ring_tip = hand_landmarks.landmark[16]
            pinky_tip = hand_landmarks.landmark[20]
            index_pip = hand_landmarks.landmark[6]
            middle_pip = hand_landmarks.landmark[10]
            ring_pip = hand_landmarks.landmark[14]
            pinky_pip = hand_landmarks.landmark[18]
            index_mcp = hand_landmarks.landmark[5]

            thumb_extended = (
                thumb_tip.y < thumb_ip.y < thumb_mcp.y
                and thumb_tip.y < index_mcp.y
                and (wrist.y - thumb_tip.y) > 0.08
            )
            folded_fingers = sum(
                [
                    index_tip.y > index_pip.y,
                    middle_tip.y > middle_pip.y,
                    ring_tip.y > ring_pip.y,
                    pinky_tip.y > pinky_pip.y,
                ]
            )
            return thumb_extended and folded_fingers >= 3
        except (AttributeError, IndexError, TypeError):
            return False

    def _is_arm_raised(self, shoulder, wrist) -> bool:
        """Return True when a visible wrist is above its shoulder."""
        return (
            shoulder.visibility > 0.5
            and wrist.visibility > 0.5
            and wrist.y < shoulder.y
        )

    def _is_wave_arm_active(self, shoulder, wrist) -> bool:
        """Allow a small vertical margin so casual waves are still considered active."""
        return (
            shoulder.visibility > 0.5
            and wrist.visibility > 0.5
            and wrist.y < (shoulder.y + 0.12)
        )

    def _update_wave_history(self, side: str, wrist_x: float, arm_raised: bool) -> None:
        """Maintain a short wrist trajectory only while the arm is raised."""
        history = self._wave_history[side]
        if arm_raised:
            history.append(wrist_x)
        else:
            history.clear()

    def _has_wave_pattern(self, history: deque) -> bool:
        """Detect a horizontal back-and-forth pattern from wrist positions."""
        if len(history) < 3:
            return False

        xs = list(history)
        amplitude = max(xs) - min(xs)
        if amplitude < 0.05:
            return False

        diffs = [xs[i] - xs[i - 1] for i in range(1, len(xs))]
        signs = []
        for diff in diffs:
            if abs(diff) < 0.008:
                continue
            signs.append(1 if diff > 0 else -1)

        direction_changes = sum(
            1 for i in range(1, len(signs)) if signs[i] != signs[i - 1]
        )
        total_travel = sum(abs(diff) for diff in diffs)
        strong_sweep = any(abs(diff) >= 0.035 for diff in diffs)

        return (
            direction_changes >= 1
            or (strong_sweep and total_travel >= 0.10)
        )

    def _is_waving(self, landmarks) -> bool:
        """Check if pose indicates a real waving motion rather than just a raised arm."""
        left_shoulder = landmarks[11]
        right_shoulder = landmarks[12]
        left_wrist = landmarks[15]
        right_wrist = landmarks[16]

        self._update_wave_history(
            "left", left_wrist.x, self._is_wave_arm_active(left_shoulder, left_wrist)
        )
        self._update_wave_history(
            "right", right_wrist.x, self._is_wave_arm_active(right_shoulder, right_wrist)
        )

        return self._has_wave_pattern(self._wave_history["left"]) or self._has_wave_pattern(
            self._wave_history["right"]
        )

    def get_frame_count(self) -> int:
        """Get total number of frames processed."""
        return self.frame_count

    def reset(self) -> None:
        """Reset frame counter and internal temporal state."""
        self.frame_count = 0
        self._prev_motion_frame = None
        self._prev_left_wrist_x = None
        self._prev_right_wrist_x = None
        for history in self._wave_history.values():
            history.clear()
