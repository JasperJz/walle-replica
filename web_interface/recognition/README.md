# OpenCV Gesture Recognition Module

This module provides gesture and pose recognition capabilities for the Wall-E robot, enabling it to recognize human actions from video input and respond with appropriate robot movements.

## Features

- **Hand Gesture Recognition**: Detects thumbs up, pointing, and other hand gestures
- **Pose Recognition**: Detects waving, clapping, and other full-body gestures
- **Action Enums**: Strongly-typed action definitions (wave, touch, clap, etc.)
- **Debouncing**: Prevents repeated recognition of the same gesture within a time window
- **Camera Abstraction**: Support for multiple camera implementations
  - **MockCamera**: For local Mac/Linux development with webcam
  - **PiCamera**: For Raspberry Pi with CSI camera
- **Thread-safe**: Recognition runs in a separate thread
- **Callback System**: Register callbacks to handle recognized gestures

## Architecture

```
GesturePipeline (main orchestrator)
├── Camera (abstract base + implementations)
│   ├── MockCamera (for Mac development)
│   └── PiCamera (for Raspberry Pi)
├── GestureRecognizer (OpenCV + MediaPipe)
├── Debouncer (cooldown between gestures)
└── ArduinoInterface (send commands to robot)
```

## Quick Start

### Mac Development Setup

1. **Install dependencies**:
   ```bash
   pip install -r requirements_recognition.txt
   ```

2. **Test with mock camera**:
   ```bash
   python test_recognition_mac.py
   ```
   
   Press 'q' to quit, 's' to view statistics.

### Integration with Flask App

Add recognition to your Flask app:

```python
from recognition.cameras.mock_camera import MockCamera
from recognition.pipelines.gesture_pipeline import GesturePipeline
from recognition.arduino_interface import ArduinoInterface

# Create pipeline with mock camera for Mac development
camera = MockCamera(camera_index=0, width=640, height=480)
pipeline = GesturePipeline(camera, confidence_threshold=0.5)

# Create Arduino interface
arduino = ArduinoInterface(port="/dev/ttyUSB0")
arduino.connect()

# Register callback to send commands to robot
def on_gesture(result):
    arduino.send_action(result)

pipeline.register_callback(on_gesture)
pipeline.start()
```

## Supported Actions

- `WAVE`: Full arm wave gesture
- `WAVE_LEFT`: Wave with left arm
- `WAVE_RIGHT`: Wave with right arm
- `TOUCH`: Hand touching/reaching gesture
- `THUMBS_UP`: Thumbs up hand gesture
- `CLAP`: Clapping gesture
- `POINT`: Pointing gesture
- `IDLE`: No specific gesture detected (default)

## Configuration

### Debouncer Cooldowns

Configure debouncer cooldowns per action:

```python
from recognition.action_enum import ActionEnum
from recognition.debouncer import Debouncer

debouncer = Debouncer(
    default_cooldown=1.0,  # 1 second default
    per_action_cooldowns={
        ActionEnum.WAVE: 0.5,   # 0.5 second for wave
        ActionEnum.CLAP: 2.0,   # 2 seconds for clap
    }
)
```

### Recognition Confidence

Adjust confidence threshold:

```python
# Higher threshold = fewer false positives, may miss some gestures
recognizer = GestureRecognizer(confidence_threshold=0.7)
```

## Performance Considerations

- **Processing**: MediaPipe runs on CPU. For better performance on Pi, consider:
  - Using model_complexity=0 (faster, less accurate)
  - Processing every other frame
  - Running on GPU if available

- **Camera**: 
  - MockCamera: Good for development, ~30 FPS typical
  - PiCamera: ~20-30 FPS with CSI ribbon cable

## Troubleshooting

### Camera Not Detected (Mac)

List available cameras:
```python
from recognition.cameras.mock_camera import MockCamera
print(MockCamera.list_available_cameras())
```

### MediaPipe Not Installed

On Raspberry Pi, use headless OpenCV version:
```bash
pip install opencv-python-headless mediapipe
```

### Poor Recognition

- Increase lighting
- Adjust confidence threshold lower (more sensitive)
- Move closer to camera
- Ensure camera is well-calibrated

## File Structure

```
recognition/
├── __init__.py
├── action_enum.py           # Action definitions
├── gesture_recognizer.py    # Main recognition logic
├── debouncer.py             # Gesture debouncing
├── arduino_interface.py     # Robot control interface
├── cameras/
│   ├── __init__.py
│   ├── base_camera.py       # Abstract camera interface
│   └── mock_camera.py       # Mac/Linux webcam implementation
└── pipelines/
    ├── __init__.py
    └── gesture_pipeline.py  # Main orchestration pipeline
```

## Adding New Gestures

1. Add action to `ActionEnum` in `action_enum.py`
2. Add recognition logic to `GestureRecognizer._recognize_*` methods
3. Add Arduino command mapping in `ArduinoInterface.ACTION_COMMANDS`

## Future Enhancements

- [ ] TensorFlow Lite models for faster inference
- [ ] GPU acceleration options
- [ ] Custom gesture training pipeline
- [ ] Multi-person detection and tracking
- [ ] Real-time visualization and debugging UI
