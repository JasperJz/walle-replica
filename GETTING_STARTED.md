# 🚀 Getting Started with OpenCV Gesture Recognition

你好! 👋 这是一个快速入门指南，帮你立即开始使用新增的视频识别功能。

## 第一步：安装依赖 (2 分钟)

```bash
cd web_interface
pip install -r requirements_recognition.txt
```

这会安装：
- `opencv-python` - 视频处理
- `mediapipe` - 姿态和手部检测

## 第二步：在 Mac 上测试 (5 分钟)

```bash
python test_recognition_mac.py
```

你应该看到：
1. 一个显示摄像头画面的窗口
2. 左上角显示处理的帧数和识别的手势数
3. 控制台输出识别到的动作

**按键:**
- `q` - 退出
- `s` - 显示统计信息

**期望输出:**
```
INFO - Pipeline running. Press 'q' to quit, 's' for stats
INFO - ✓ GESTURE RECOGNIZED: wave (confidence: 0.75)
INFO - ✓ GESTURE RECOGNIZED: clap (confidence: 0.68)
```

## 第三步：理解架构

```
你的 MacBook 摄像头
       ↓
   [MockCamera] - 使用 OpenCV 捕获
       ↓
[GestureRecognizer] - MediaPipe 检测姿态/手部
       ↓
  [Debouncer] - 防止 1 秒内重复识别
       ↓
  识别结果 (wave, clap, etc.)
       ↓
  [可选] 通过串口发送到 Arduino 机器人
```

## 第四步：集成到 Flask 应用 (可选现在或稍后)

如果你想立即集成，编辑 `app.py` 并添加：

```python
from recognition.integration_example import create_recognition_pipeline, create_recognition_routes

# 在 app 初始化后添加
recognition_pipeline = create_recognition_pipeline(
    use_mock_camera=True,  # Mac 上使用 Mock，树莓派上改为 False
    confidence_threshold=0.5,
    debounce_cooldown=1.0,
)

if recognition_pipeline:
    # 添加 API 端点
    create_recognition_routes(app, recognition_pipeline)
    
    # 可选：自动启动
    # recognition_pipeline.start()
```

然后你可以通过 API 控制：

```bash
# 启动识别
curl -X POST http://localhost:5000/api/recognition/start

# 获取状态
curl http://localhost:5000/api/recognition/status

# 停止识别
curl -X POST http://localhost:5000/api/recognition/stop
```

## 支持的手势

| 动作 | 说明 |
|------|------|
| WAVE | 挥手 |
| WAVE_LEFT | 左手挥手 |
| WAVE_RIGHT | 右手挥手 |
| CLAP | 鼓掌 |
| THUMBS_UP | 竖起大拇指 |
| POINT | 指向 |
| TOUCH | 接触/伸手 |
| IDLE | 无特定手势 |

## 配置选项

### 调整检测灵敏度

```python
# 更严格的检测（假阳性少，但可能漏掉一些手势）
pipeline = GesturePipeline(
    camera=camera,
    confidence_threshold=0.7,  # 默认是 0.5
    debounce_cooldown=1.5,      # 增加冷却时间
)
```

### 调整防抖时间

如果你觉得识别太频繁或太稀疏：

```python
debouncer = Debouncer(
    default_cooldown=0.5,  # 更敏感
    per_action_cooldowns={
        ActionEnum.WAVE: 0.3,   # 挥手可以更频繁
        ActionEnum.CLAP: 2.0,   # 鼓掌需要更长时间
    }
)
```

## 常见问题

### Q: 摄像头找不到？

```python
from recognition.cameras.mock_camera import MockCamera
cameras = MockCamera.list_available_cameras()
print(f"Available: {cameras}")
```

### Q: 识别不太准？

1. **增加光线** - 光线不足时效果差
2. **调低阈值** - `confidence_threshold=0.3` 更灵敏
3. **离摄像头近点** - MediaPipe 需要清晰的图像

### Q: 需要连接机器人吗？

不需要！完整的管道在没有 Arduino 的情况下也能工作。识别部分和机器人控制完全分离。

## 对 Arduino 的回应 (可选)

如果想让机器人做出反应：

```python
from recognition.arduino_interface import ArduinoInterface

arduino = ArduinoInterface(port="/dev/ttyUSB0")  # 或 COM3（Windows）
arduino.connect()

def on_gesture(result):
    arduino.send_action(result)
    print(f"发送命令到机器人: {result.action.value}")

pipeline.register_callback(on_gesture)
```

命令映射：
- wave → 'x'
- clap → 'c'  
- point → 'p'
- thumbs_up → 'u'
- 等等...

## 下一步

1. ✅ **现在就尝试**: `python test_recognition_mac.py`
2. 📖 **阅读详细文档**: `recognition/README.md`
3. 🔗 **集成 Flask**: `RECOGNITION_INTEGRATION.md`
4. 🤖 **连接机器人**: See Arduino section
5. 🎯 **部署到树莓派**: Change `use_mock_camera=False`

## 文件查找

```
# 核心模块
web_interface/recognition/
├── gesture_recognizer.py    # 识别逻辑
├── debouncer.py             # 防抖
├── action_enum.py           # 手势定义
└── cameras/mock_camera.py   # Mac 摄像头

# 文档
RECOGNITION_INTEGRATION.md   # Flask 集成指南
recognition/README.md        # 详细说明
OPENCV_INTEGRATION_SUMMARY.md # 架构总结

# 测试
test_recognition_mac.py      # 立即运行这个！
```

## 需要帮助？

- **模块问题**: 查看 `recognition/README.md`
- **Flask 集成**: 查看 `RECOGNITION_INTEGRATION.md`
- **架构**:查看 `OPENCV_INTEGRATION_SUMMARY.md`
- **代码**:所有代码都有详细注释

---

**准备好了吗?** 运行这个命令开始吧! 🎬

```bash
cd web_interface
python test_recognition_mac.py
```
