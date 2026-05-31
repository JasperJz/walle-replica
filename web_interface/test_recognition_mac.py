"""
Test script for gesture recognition on Mac with MockCamera.

This script demonstrates how to test the recognition pipeline locally
using the Mac built-in webcam without needing a Raspberry Pi.

Usage:
    python test_recognition_mac.py
    
Press 'q' to quit, 's' to print stats.
"""

import cv2
import logging
import sys
import time
from threading import Thread, Event
from recognition.cameras.mock_camera import MockCamera
from recognition.pipelines.gesture_pipeline import GesturePipeline
from recognition.action_enum import RecognitionResult, ActionEnum

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def on_gesture_recognized(result: RecognitionResult) -> None:
    """Callback when gesture is recognized."""
    logger.info(f"✓ GESTURE RECOGNIZED: {result.action.value} (confidence: {result.confidence:.2f})")


def draw_text_on_frame(frame, text: str, position=(10, 30), color=(0, 255, 0), fontsize=0.7):
    """Draw text on frame for visualization."""
    cv2.putText(
        frame,
        text,
        position,
        cv2.FONT_HERSHEY_SIMPLEX,
        fontsize,
        color,
        2,
        cv2.LINE_AA
    )


def main():
    """Main test function."""
    logger.info("Starting gesture recognition test on Mac...")
    logger.info("=" * 70)
    
    # Create mock camera
    logger.info("Initializing MockCamera...")
    camera = MockCamera(camera_index=0, width=640, height=480)
    
    # Create pipeline
    logger.info("Initializing recognition pipeline...")
    pipeline = GesturePipeline(
        camera=camera,
        confidence_threshold=0.5,
        debounce_cooldown=1.0,
    )
    
    # Register callback
    pipeline.register_callback(on_gesture_recognized)
    
    # Start pipeline
    logger.info("Starting pipeline...")
    if not pipeline.start():
        logger.error("Failed to start pipeline")
        logger.error("Possible solutions:")
        logger.error("  1. Check if a camera is connected to your Mac")
        logger.error("  2. Check camera permissions: System Preferences → Security & Privacy → Camera")
        logger.error("  3. Try closing other apps using the camera")
        logger.error("  4. Restart your Mac")
        return 1
    
    logger.info("Pipeline running successfully!")
    logger.info("-" * 70)
    logger.info("📹 Camera is now active - show your gestures to the camera!")
    logger.info("   - WAVE: Raise your arm and move it side to side")
    logger.info("   - CLAP: Bring your hands together")
    logger.info("   - POINT: Extend one arm with index finger pointing")
    logger.info("   - THUMBS UP: Show thumbs up gesture")
    logger.info("-" * 70)
    logger.info("Controls:")
    logger.info("  'q' - Quit the program")
    logger.info("  's' - Print statistics")
    logger.info("=" * 70)
    
    # Flag to track if window has been created
    window_created = False
    last_stat_print = time.time()
    
    try:
        while True:
            # Get frame from camera (from the internal capture in pipeline)
            # We need to get frames from the camera directly to display them
            frame = camera.get_frame()
            
            if frame is None:
                # Camera not ready yet, wait a bit
                time.sleep(0.01)
                continue
            
            # Get pipeline stats
            stats = pipeline.get_stats()
            
            # Add info overlay on the frame
            draw_text_on_frame(frame, f"Frames: {stats['frames_processed']}", (10, 30), color=(0, 255, 0))
            draw_text_on_frame(frame, f"Gestures: {stats['gestures_recognized']}", (10, 60), color=(0, 255, 0))
            draw_text_on_frame(frame, f"FPS: {stats['camera_fps']:.1f}", (10, 90), color=(0, 255, 0))
            
            # Add status info
            status_color = (0, 255, 0) if stats['running'] else (0, 0, 255)
            status_text = "● RUNNING" if stats['running'] else "● STOPPED"
            draw_text_on_frame(frame, status_text, (10, frame.shape[0] - 20), color=status_color)
            
            # Display frame
            window_name = "Gesture Recognition - Mac Test"
            cv2.imshow(window_name, frame)
            window_created = True
            
            # Handle keyboard input
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                logger.info("\nQuit requested by user")
                break
            elif key == ord('s'):
                logger.info("\nPipeline Statistics:")
                logger.info("-" * 50)
                for stat_key, value in stats.items():
                    logger.info(f"  {stat_key}: {value}")
                logger.info("-" * 50)
                last_stat_print = time.time()
    
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user (Ctrl+C)")
    
    except Exception as e:
        logger.error(f"\nError in main loop: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        logger.info("\nCleaning up...")
        logger.info("-" * 70)
        
        # Stop pipeline
        if pipeline.is_running():
            pipeline.stop()
        
        # Close OpenCV window
        if window_created:
            cv2.destroyAllWindows()
        
        # Print final statistics
        final_stats = pipeline.get_stats()
        logger.info("Final Statistics:")
        logger.info(f"  Total frames processed: {final_stats['frames_processed']}")
        logger.info(f"  Total gestures recognized: {final_stats['gestures_recognized']}")
        if final_stats['frames_processed'] > 0:
            logger.info(f"  Recognition rate: {final_stats['avg_recognition_rate']*100:.2f}%")
        logger.info("=" * 70)
        logger.info("Thank you for testing! Goodbye! 👋")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
