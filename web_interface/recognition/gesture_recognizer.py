"""
Gesture and pose recognition using OpenCV and MediaPipe.

This module detects human poses and gestures from video frames.
"""

import time
import logging
from typing import Optional, Tuple
import numpy as np
import cv2

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
    
    Uses MediaPipe for pose and hand detection, with configurable
    thresholds for gesture recognition.
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
            self.mp_drawing = mp.solutions.drawing_utils
            
            # Initialize pose detector
            self.pose = self.mp_pose.Pose(
                static_image_mode=False,
                model_complexity=0,  # 0 for faster, 1 for better accuracy
                smooth_landmarks=True,
            )
            
            # Initialize hand detector
            self.hands = self.mp_hands.Hands(
                static_image_mode=False,
                max_num_hands=2,
                min_detection_confidence=confidence_threshold,
            )
        
        self.confidence_threshold = confidence_threshold
        self.frame_count = 0
    
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
        
        # Convert BGR to RGB
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Detect pose
        pose_results = self.pose.process(rgb_frame)
        hand_results = self.hands.process(rgb_frame)
        
        # Try to recognize gestures in order of specificity
        if hand_results.multi_hand_landmarks:
            result = self._recognize_hand_gesture(hand_results, timestamp)
            if result:
                return result
        
        if pose_results.pose_landmarks:
            result = self._recognize_pose_gesture(pose_results, timestamp)
            if result:
                return result
        
        # Default to idle if we detected a person but no specific gesture
        if pose_results.pose_landmarks:
            return RecognitionResult(
                action=ActionEnum.IDLE,
                confidence=0.3,
                timestamp=timestamp,
                frame_number=self.frame_count,
            )
        
        return None
    
    def _recognize_hand_gesture(self, hand_results, timestamp: float) -> Optional[RecognitionResult]:
        """Recognize hand-based gestures."""
        if not hand_results.multi_hand_landmarks:
            return None
        
        # For each detected hand
        for hand_landmarks, handedness in zip(
            hand_results.multi_hand_landmarks,
            hand_results.multi_handedness
        ):
            # Simple gesture detection based on hand shape
            # Thumbs up: thumb extended above other fingers
            if self._is_thumbs_up(hand_landmarks):
                return RecognitionResult(
                    action=ActionEnum.THUMBS_UP,
                    confidence=0.7,
                    timestamp=timestamp,
                    frame_number=self.frame_count,
                )
            
            # Pointing: index finger extended
            if self._is_pointing(hand_landmarks):
                return RecognitionResult(
                    action=ActionEnum.POINT,
                    confidence=0.6,
                    timestamp=timestamp,
                    frame_number=self.frame_count,
                )
        
        return None
    
    def _recognize_pose_gesture(self, pose_results, timestamp: float) -> Optional[RecognitionResult]:
        """Recognize body pose-based gestures."""
        landmarks = pose_results.pose_landmarks.landmark
        
        # Wave detection: arm raised, moving side to side
        if self._is_waving(landmarks):
            return RecognitionResult(
                action=ActionEnum.WAVE,
                confidence=0.65,
                timestamp=timestamp,
                frame_number=self.frame_count,
            )
        
        # Clap detection: both hands coming together
        if self._is_clapping(landmarks):
            return RecognitionResult(
                action=ActionEnum.CLAP,
                confidence=0.6,
                timestamp=timestamp,
                frame_number=self.frame_count,
            )
        
        return None
    
    def _is_thumbs_up(self, hand_landmarks) -> bool:
        """Check if hand pose indicates thumbs up gesture."""
        # Simplified check: thumb tip above other fingertips
        thumb = hand_landmarks[4]  # Thumb tip
        index = hand_landmarks[8]   # Index finger tip
        
        # Thumb should be above index finger
        return thumb.y < index.y
    
    def _is_pointing(self, hand_landmarks) -> bool:
        """Check if hand pose indicates pointing gesture."""
        # Simplified check: index finger extended, others closed
        index = hand_landmarks[8]   # Index finger tip
        middle = hand_landmarks[12] # Middle finger tip
        ring = hand_landmarks[16]   # Ring finger tip
        
        # Index extended further than others
        return index.y < middle.y and index.y < ring.y
    
    def _is_waving(self, landmarks) -> bool:
        """Check if pose indicates waving gesture."""
        # Check if one arm is raised (shoulder, elbow, wrist)
        # Shoulders
        left_shoulder = landmarks[11]
        right_shoulder = landmarks[12]
        
        # Elbows
        left_elbow = landmarks[13]
        right_elbow = landmarks[14]
        
        # Wrists
        left_wrist = landmarks[15]
        right_wrist = landmarks[16]
        
        # Check if either arm is raised (wrist above shoulder)
        left_arm_raised = (left_wrist.y < left_shoulder.y and 
                          left_elbow.visibility > 0.5 and
                          left_wrist.visibility > 0.5)
        
        right_arm_raised = (right_wrist.y < right_shoulder.y and
                           right_elbow.visibility > 0.5 and
                           right_wrist.visibility > 0.5)
        
        return left_arm_raised or right_arm_raised
    
    def _is_clapping(self, landmarks) -> bool:
        """Check if pose indicates clapping gesture."""
        # Simplified: both wrists close to each other at face level
        left_wrist = landmarks[15]
        right_wrist = landmarks[16]
        nose = landmarks[0]
        
        # Wrists close together horizontally and near face level
        wrist_distance = abs(left_wrist.x - right_wrist.x)
        wrists_at_face = (left_wrist.y < nose.y and right_wrist.y < nose.y)
        wrists_close = wrist_distance < 0.1
        
        return wrists_close and wrists_at_face
    
    def get_frame_count(self) -> int:
        """Get total number of frames processed."""
        return self.frame_count
    
    def reset(self) -> None:
        """Reset frame counter and any internal state."""
        self.frame_count = 0
