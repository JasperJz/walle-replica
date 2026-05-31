"""
Debouncer for gesture recognition.

Prevents repeated recognition of the same gesture within a configurable time window.
"""

import time
from typing import Optional, Dict
from .action_enum import ActionEnum, RecognitionResult


class Debouncer:
    """
    Debounces gesture recognition results to prevent repeated triggers.
    
    Useful for avoiding multiple recognitions of the same action within
    a short time window (e.g., preventing "wave" from triggering 10 times/second).
    """
    
    def __init__(self, default_cooldown: float = 1.0, per_action_cooldowns: Optional[Dict[ActionEnum, float]] = None):
        """
        Initialize debouncer.
        
        Args:
            default_cooldown: Default cooldown time in seconds for all actions
            per_action_cooldowns: Optional dict with action-specific cooldowns
        """
        self.default_cooldown = default_cooldown
        self.per_action_cooldowns = per_action_cooldowns or {}
        self.last_action_time: Dict[ActionEnum, float] = {}
    
    def should_process(self, result: RecognitionResult) -> bool:
        """
        Check if the recognition result should be processed (not debounced).
        
        Args:
            result: The recognition result to check
            
        Returns:
            True if the action should be processed, False if debounced
        """
        action = result.action
        current_time = time.time()
        
        # Get cooldown for this action (use per-action or default)
        cooldown = self.per_action_cooldowns.get(action, self.default_cooldown)
        
        # Check if action is in cooldown period
        if action in self.last_action_time:
            time_since_last = current_time - self.last_action_time[action]
            if time_since_last < cooldown:
                return False
        
        # Update last action time and allow processing
        self.last_action_time[action] = current_time
        return True
    
    def reset(self, action: Optional[ActionEnum] = None) -> None:
        """
        Reset the debouncer for a specific action or all actions.
        
        Args:
            action: Specific action to reset, or None to reset all
        """
        if action is None:
            self.last_action_time.clear()
        elif action in self.last_action_time:
            del self.last_action_time[action]
    
    def get_time_until_ready(self, action: ActionEnum) -> float:
        """
        Get seconds until an action is ready to be recognized again.
        
        Args:
            action: The action to check
            
        Returns:
            Seconds remaining in cooldown (0 if ready)
        """
        if action not in self.last_action_time:
            return 0.0
        
        cooldown = self.per_action_cooldowns.get(action, self.default_cooldown)
        time_since_last = time.time() - self.last_action_time[action]
        remaining = cooldown - time_since_last
        
        return max(0.0, remaining)
