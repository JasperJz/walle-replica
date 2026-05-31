"""
Example integration of gesture recognition with Flask app.

This module shows how to integrate the GesturePipeline with the
existing Wall-E Flask web interface.

Usage in app.py:
    from recognition.integration_example import create_recognition_pipeline
    
    recognition_pipeline = create_recognition_pipeline(
        use_mock_camera=True,  # For Mac development
        arduino_port="/dev/ttyUSB0"
    )
    
    if recognition_pipeline:
        recognition_pipeline.start()
"""

import logging
import os
from typing import Optional

from .cameras.mock_camera import MockCamera
from .pipelines.gesture_pipeline import GesturePipeline
from .arduino_interface import ArduinoInterface

logger = logging.getLogger(__name__)


def create_recognition_pipeline(
    use_mock_camera: bool = False,
    arduino_port: str = "/dev/ttyUSB0",
    confidence_threshold: float = 0.5,
    debounce_cooldown: float = 1.0,
) -> Optional[GesturePipeline]:
    """
    Factory function to create and configure a recognition pipeline.
    
    Args:
        use_mock_camera: Use MockCamera for development (True) or real Pi camera (False)
        arduino_port: Serial port for Arduino connection
        confidence_threshold: Minimum confidence for gesture recognition
        debounce_cooldown: Cooldown between recognized gestures
        
    Returns:
        Configured GesturePipeline instance, or None if creation failed
    """
    try:
        # Create appropriate camera
        if use_mock_camera:
            logger.info("Creating MockCamera for development")
            camera = MockCamera(camera_index=0, width=640, height=480)
        else:
            # For Raspberry Pi, would create PiCamera here
            logger.error("PiCamera not yet implemented - use use_mock_camera=True")
            return None
        
        # Create pipeline
        pipeline = GesturePipeline(
            camera=camera,
            confidence_threshold=confidence_threshold,
            debounce_cooldown=debounce_cooldown,
        )
        
        # Create and connect Arduino interface
        arduino = ArduinoInterface(port=arduino_port)
        
        # Register callback to send actions to Arduino
        def send_to_arduino(result):
            if arduino.is_connected():
                arduino.send_action(result)
            else:
                # If Arduino not connected, just log (useful for development)
                logger.info(f"Would send to Arduino: {result.action.value}")
        
        pipeline.register_callback(send_to_arduino)
        
        # Try to connect to Arduino (optional - app works without it)
        if arduino.connect():
            logger.info("Connected to Arduino")
        else:
            logger.warning("Arduino not connected (optional for dev mode)")
        
        return pipeline
    
    except Exception as e:
        logger.error(f"Failed to create recognition pipeline: {e}")
        return None


# Example Flask endpoints
def create_recognition_routes(app, pipeline: GesturePipeline):
    """
    Create Flask routes for recognition control and status.
    
    Args:
        app: Flask application instance
        pipeline: GesturePipeline instance
    """
    
    @app.route("/api/recognition/start", methods=["POST"])
    def start_recognition():
        """Start the recognition pipeline."""
        if pipeline.is_running():
            return {"success": False, "error": "Already running"}, 400
        
        if pipeline.start():
            return {"success": True, "message": "Recognition started"}
        else:
            return {"success": False, "error": "Failed to start"}, 500
    
    @app.route("/api/recognition/stop", methods=["POST"])
    def stop_recognition():
        """Stop the recognition pipeline."""
        if not pipeline.is_running():
            return {"success": False, "error": "Not running"}, 400
        
        if pipeline.stop():
            return {"success": True, "message": "Recognition stopped"}
        else:
            return {"success": False, "error": "Failed to stop"}, 500
    
    @app.route("/api/recognition/status", methods=["GET"])
    def get_recognition_status():
        """Get recognition pipeline status and statistics."""
        return pipeline.get_stats()
    
    @app.route("/api/recognition/reset", methods=["POST"])
    def reset_recognition():
        """Reset recognition statistics."""
        pipeline.reset_stats()
        return {"success": True, "message": "Statistics reset"}
