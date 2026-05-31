# OpenCV Gesture Recognition Integration Guide

This guide explains how to integrate the gesture recognition module into the existing Wall-E Flask web interface.

## Quick Start (5 minutes)

### 1. Install Dependencies

```bash
cd web_interface
pip install -r requirements_recognition.txt
```

For Raspberry Pi (headless):
```bash
pip install opencv-python-headless mediapipe pyserial
```

### 2. Test on Mac with Mock Camera

```bash
python test_recognition_mac.py
```

Press 'q' to quit, 's' to see statistics.

### 3. Basic Integration with Flask App

Modify your `app.py` to include:

```python
from recognition.integration_example import create_recognition_pipeline, create_recognition_routes

# Initialize recognition pipeline (use_mock_camera=True for Mac dev)
recognition_pipeline = create_recognition_pipeline(
    use_mock_camera=True,  # Change to False for Raspberry Pi
    arduino_port="/dev/ttyUSB0",
    confidence_threshold=0.5,
    debounce_cooldown=1.0,
)

if recognition_pipeline:
    # Add API endpoints for recognition control
    create_recognition_routes(app, recognition_pipeline)
    
    # Auto-start on app initialization (optional)
    # recognition_pipeline.start()
```

### 4. Control via API

```bash
# Start recognition
curl -X POST http://localhost:5000/api/recognition/start

# Get status
curl http://localhost:5000/api/recognition/status

# Stop recognition  
curl -X POST http://localhost:5000/api/recognition/stop

# Reset statistics
curl -X POST http://localhost:5000/api/recognition/reset
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│          Flask Web Application (app.py)             │
├─────────────────────────────────────────────────────┤
│                                                       │
│  GesturePipeline (orchestrator)                     │
│  ├─ Camera                                           │
│  │  ├─ MockCamera (Mac development)                 │
│  │  └─ PiCamera (Raspberry Pi)                      │
│  ├─ GestureRecognizer (OpenCV + MediaPipe)         │
│  ├─ Debouncer (cooldown filtering)                  │
│  └─ Callbacks (e.g., ArduinoInterface)             │
│                                                       │
└─────────────────────────────────────────────────────┘
         │
         ├─→ Video Frame ──→ Recognition ──→ Action
         │
         └─→ Serial Port ──→ Arduino ──→ Robot Movement
```

## File Structure

```
web_interface/
├── app.py (main Flask app)
├── config.py (configuration)
├── requirements_recognition.txt (dependencies)
├── test_recognition_mac.py (Mac testing script)
├── RECOGNITION_INTEGRATION.md (this file)
└── recognition/
    ├── __init__.py
    ├── action_enum.py (action definitions)
    ├── gesture_recognizer.py (OpenCV recognition)
    ├── debouncer.py (gesture cooldown)
    ├── arduino_interface.py (robot control)
    ├── integration_example.py (Flask integration helper)
    ├── README.md (module documentation)
    ├── cameras/
    │   ├── __init__.py
    │   ├── base_camera.py (abstract interface)
    │   ├── mock_camera.py (Mac webcam)
    │   └── pi_camera.py (Raspberry Pi CSI) [TODO]
    └── pipelines/
        ├── __init__.py
        └── gesture_pipeline.py (orchestration)
```

## Development Workflow

### On Mac: Test with MockCamera

1. **Install dependencies**:
   ```bash
   pip install opencv-python mediapipe
   ```

2. **Run test script**:
   ```bash
   python test_recognition_mac.py
   ```

3. **Expected output**:
   ```
   INFO - Initializing MockCamera...
   INFO - Initializing recognition pipeline...
   INFO - Starting pipeline...
   INFO - Pipeline running. Press 'q' to quit, 's' for stats
   INFO - ✓ GESTURE RECOGNIZED: wave (confidence: 0.75)
   ```

### On Raspberry Pi: Use Real Camera

1. **Install dependencies**:
   ```bash
   sudo apt-get install python3-opencv python3-dev
   pip install opencv-python-headless mediapipe
   ```

2. **Modify integration to use real camera**:
   ```python
   pipeline = create_recognition_pipeline(use_mock_camera=False)
   ```

3. **Check camera connection**:
   ```bash
   libcamera-hello --list-cameras
   ```

## Configuration Options

### GesturePipeline

```python
from recognition.pipelines.gesture_pipeline import GesturePipeline
from recognition.cameras.mock_camera import MockCamera

camera = MockCamera(camera_index=0, width=640, height=480)
pipeline = GesturePipeline(
    camera=camera,
    confidence_threshold=0.5,  # 0.0-1.0, higher = stricter
    debounce_cooldown=1.0,      # seconds between same gesture
)

# Register custom callback
def my_callback(result):
    print(f"Recognized: {result.action} @ {result.confidence:.2%}")

pipeline.register_callback(my_callback)
pipeline.start()
```

### Debouncer

```python
from recognition.debouncer import Debouncer
from recognition.action_enum import ActionEnum

debouncer = Debouncer(
    default_cooldown=1.0,
    per_action_cooldowns={
        ActionEnum.WAVE: 0.5,    # Allow wave more frequently
        ActionEnum.CLAP: 2.0,    # Require longer between claps
    }
)
```

### Recognition Parameters

Adjust in `GestureRecognizer`:
- `model_complexity`: 0 (fast) or 1 (accurate)
- `smooth_landmarks`: Boolean for temporal smoothing
- `confidence_threshold`: Minimum detection confidence

## Arduino Integration

### Command Mapping

Recognized gestures are mapped to single-character Arduino commands:

| Gesture | Command |
|---------|---------|
| WAVE | 'x' |
| WAVE_LEFT | 'y' |
| WAVE_RIGHT | 'z' |
| TOUCH | 't' |
| THUMBS_UP | 'u' |
| CLAP | 'c' |
| POINT | 'p' |
| IDLE | ' ' (space) |

### Serial Connection

```python
from recognition.arduino_interface import ArduinoInterface

arduino = ArduinoInterface(port="/dev/ttyUSB0", baudrate=115200)
arduino.connect()

# Send action to robot
result = RecognitionResult(action=ActionEnum.WAVE, confidence=0.8, timestamp=0)
arduino.send_action(result)
```

### Add to Flask App

```python
# In app.py
arduino_interface = ArduinoInterface(port="/dev/ttyUSB0")
arduino_interface.connect()

def on_gesture(result):
    arduino_interface.send_action(result)

pipeline.register_callback(on_gesture)
```

## Troubleshooting

### Camera Not Working

**Mac:**
```python
from recognition.cameras.mock_camera import MockCamera
available = MockCamera.list_available_cameras()
print(f"Available cameras: {available}")
# Create with available camera
camera = MockCamera(camera_index=available[0])
```

**Raspberry Pi:**
```bash
# Check camera connection
libcamera-hello --list-cameras

# Test with libcamera
libcamera-jpeg -o test.jpg
```

### Recognition Not Detecting Gestures

1. **Check lighting**: Ensure adequate lighting
2. **Increase exposure**: Adjust camera settings
3. **Lower confidence threshold**: `confidence_threshold=0.3`
4. **Increase debounce**: Give more time between gestures

### MediaPipe Errors

```
ModuleNotFoundError: No module named 'mediapipe'
```

Solution:
```bash
pip install mediapipe==0.10.9
```

### Poor Performance

- Use `model_complexity=0` for faster processing
- Process every other frame if needed
- Reduce resolution (e.g., 320x240)
- Consider GPU acceleration on Pi with Coral TPU

## Performance Metrics

Expected performance on different platforms:

| Platform | FPS | Latency | Memory |
|----------|-----|---------|--------|
| Mac (M1) | 25-30 | 50-100ms | ~300MB |
| Raspberry Pi 4 | 15-20 | 100-200ms | ~400MB |
| Raspberry Pi Zero | 5-10 | 200-400ms | ~200MB |

## Advanced: Custom Gestures

To add new gesture recognition:

1. **Add to ActionEnum** (action_enum.py):
   ```python
   class ActionEnum(str, Enum):
       MY_GESTURE = "my_gesture"
   ```

2. **Add recognition logic** (gesture_recognizer.py):
   ```python
   def _recognize_my_gesture(self, landmarks):
       # Implement gesture detection logic
       if condition_met:
           return RecognitionResult(
               action=ActionEnum.MY_GESTURE,
               confidence=0.7,
               timestamp=time.time(),
           )
   ```

3. **Add Arduino command** (arduino_interface.py):
   ```python
   ACTION_COMMANDS = {
       ActionEnum.MY_GESTURE: "m",
       ...
   }
   ```

4. **Add Arduino sketch** (wall-e.ino):
   ```cpp
   case 'm':  // my gesture
       // Perform robot action
       break;
   ```

## Next Steps

- [ ] Test on Mac with mock camera
- [ ] Install OpenCV and MediaPipe
- [ ] Run test_recognition_mac.py
- [ ] Integrate with Flask app
- [ ] Connect Arduino for real-time control
- [ ] Fine-tune gesture detection thresholds
- [ ] Deploy to Raspberry Pi

## Support & Issues

For issues or questions:
1. Check the recognition/README.md for detailed module documentation
2. Review gesture_recognizer.py for recognition logic
3. Check logs: Enable DEBUG logging for detailed output
4. Run test script to isolate issues

## References

- MediaPipe: https://mediapipe.dev/
- OpenCV: https://opencv.org/
- PySerial: https://github.com/pyserial/pyserial
