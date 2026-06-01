"""
Interface for communicating recognized actions to the Arduino.

Sends serial commands to control the robot based on recognized gestures.
"""

import logging
from typing import Optional

try:
    from serial import Serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

from .action_enum import ActionEnum, RecognitionResult

logger = logging.getLogger(__name__)


class ArduinoInterface:
    """Interface to send recognized actions to Arduino."""
    
    # Mapping from ActionEnum to Arduino command character
    ACTION_COMMANDS = {
        ActionEnum.WAVE: "x",
        ActionEnum.THUMBS_UP: "u",
        ActionEnum.OPEN_PALM: "p",
        ActionEnum.TOUCH: "t",
        ActionEnum.IDLE: " ",  # Space for idle
    }
    
    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 115200):
        """
        Initialize Arduino interface.
        
        Args:
            port: Serial port path
            baudrate: Serial communication speed
        """
        self.port = port
        self.baudrate = baudrate
        self.serial: Optional[object] = None
        self._connected = False
        
        if not SERIAL_AVAILABLE:
            logger.warning("pyserial not available. Install with: pip install pyserial")
    
    def connect(self) -> bool:
        """
        Connect to Arduino via serial port.
        
        Returns:
            True if connected, False otherwise
        """
        if not SERIAL_AVAILABLE:
            logger.warning("Cannot connect - pyserial not installed")
            return False
        
        try:
            self.serial = Serial(self.port, self.baudrate, timeout=1)
            self._connected = True
            logger.info(f"Connected to Arduino on {self.port} at {self.baudrate} baud")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Arduino: {e}")
            return False
    
    def disconnect(self) -> bool:
        """
        Disconnect from Arduino.
        
        Returns:
            True if disconnected, False otherwise
        """
        try:
            if self.serial is not None:
                self.serial.close()
            self._connected = False
            logger.info("Disconnected from Arduino")
            return True
        except Exception as e:
            logger.error(f"Error disconnecting from Arduino: {e}")
            return False
    
    def is_connected(self) -> bool:
        """Check if connected to Arduino."""
        return self._connected and self.serial is not None
    
    def send_action(self, result: RecognitionResult) -> bool:
        """
        Send recognized action to Arduino.
        
        Args:
            result: Recognition result containing the action to send
            
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.is_connected():
            logger.warning("Arduino not connected, cannot send action")
            return False
        
        # Get command for action
        command = self.ACTION_COMMANDS.get(result.action)
        if command is None:
            logger.warning(f"Unknown action: {result.action}")
            return False
        
        try:
            self.serial.write(command.encode())
            logger.info(f"Sent action to Arduino: {result.action.value} ('{command}')")
            return True
        except Exception as e:
            logger.error(f"Error sending action to Arduino: {e}")
            return False
