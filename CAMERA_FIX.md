# 📹 摄像头修复说明

## 问题

摄像头能打开，但一下就关掉了。

## 原因已识别 ✓

1. **MockCamera 连接验证过于严格** - 只读取一帧，容易失败
2. **缺少依赖** - MediaPipe 没有安装
3. **脚本架构问题** - 主循环和识别线程竞争摄像头

## 解决方案 ✅

### Step 1: 安装完整依赖

这是必须的！MediaPipe 是手势识别的核心：

```bash
pip install opencv-python mediapipe
```

或者使用依赖文件（可能还需要补充）：

```bash
pip install -r requirements_recognition.txt
pip install mediapipe==0.10.9
```

### Step 2: 检查摄像头权限

**重要！** Mac 上 Python 需要明确的摄像头权限：

1. 打开 **系统偏好设置**
2. 进入 **安全与隐私** → **隐私**
3. 选择左侧的 **摄像头**
4. 在右侧列表中找到 **Python** 或你的 IDE
   - 如果使用 VS Code：加入 `code` 或 `Visual Studio Code`
5. **务必勾选** ☑️ 允许访问

如果列表中没有 Python，你可能需要：
- 完全移除，然后重新添加
- 或者用完整路径找到 Python

### Step 3: 测试摄像头

在运行我们的脚本前，先验证 MacBook 摄像头本身能工作：

```bash
# 打开 Photo Booth 或 FaceTime 测试
# 如果这些能看到你的画面，说明硬件没问题
```

### Step 4: 运行改进的测试脚本

```bash
cd web_interface
python test_recognition_mac.py
```

**现在应该会看到：**

```
INFO - Starting gesture recognition test on Mac...
INFO - Initializing MockCamera...
INFO - MockCamera connected: 640x480 @ 30.0fps
INFO - Pipeline running successfully!
INFO - 📹 Camera is now active - show your gestures to the camera!
```

**窗口应该显示：**
- 你的 MacBook 摄像头实时画面
- 左上角显示: `Frames: 120`, `Gestures: 2`, `FPS: 30.0`
- 右上角显示: `● RUNNING` (绿色)

**控制：**
- 按 `q` 退出
- 按 `s` 显示统计信息
- 对着摄像头做手势，看控制台输出识别结果

## 问题排查清单

如果还有问题，逐项检查：

- [ ] 已安装 `opencv-python`？
  ```bash
  python -c "import cv2; print(cv2.__version__)"
  ```

- [ ] 已安装 `mediapipe`？
  ```bash
  python -c "import mediapipe; print(mediapipe.__version__)"
  ```

- [ ] 已在 System Preferences 中允许 Python 访问摄像头？

- [ ] 摄像头在 Photo Booth 中能工作？

- [ ] 没有其他应用占用摄像头？
  - 关闭: Zoom, FaceTime, Chrome 等

- [ ] 试过重启 Mac？

## 文件修复清单

已修复的文件：

- ✅ `recognition/cameras/mock_camera.py`
  - 改进了 `connect()` 方法
  - 添加摄像头预热（读取多个帧）
  - 更好的错误处理

- ✅ `test_recognition_mac.py`
  - 重写了主循环
  - 改进了错误消息
  - 添加了更多用户指导
  - 修复了帧获取逻辑

## 验证修复

运行以下命令验证所有依赖都正确安装：

```bash
python3 << 'PYEOF'
import sys
print("=" * 50)
print("Dependency Check")
print("=" * 50)

deps = {
    'opencv-python': 'cv2',
    'mediapipe': 'mediapipe',
}

all_ok = True
for pkg, mod in deps.items():
    try:
        __import__(mod)
        print(f"✓ {pkg}")
    except ImportError:
        print(f"✗ {pkg} - MISSING!")
        all_ok = False

if all_ok:
    print("\n✓ All dependencies installed!")
    print("\nReady to run: python test_recognition_mac.py")
else:
    print("\n✗ Some dependencies missing!")
    print("\nRun: pip install opencv-python mediapipe")

print("=" * 50)
PYEOF
```

## 预期工作流

```
1. 安装依赖
2. 检查权限
3. 运行脚本
4. 看到摄像头窗口
5. 对着摄像头做手势
6. 控制台输出手势识别结果
7. 成功！ ✓
```

## 下一步

一旦摄像头工作正常：

1. **集成到 Flask** (可选)
   - 见 `RECOGNITION_INTEGRATION.md`

2. **连接 Arduino** (可选)
   - 参考 `GETTING_STARTED.md`

3. **部署到树莓派**
   - 在 Pi 上安装 OpenCV 和 MediaPipe
   - 切换到实际摄像头

## 获得帮助

- 📖 `QUICK_FIX.md` - 快速问题排解
- 📖 `GETTING_STARTED.md` - 完整中文指南
- 📖 `recognition/README.md` - 模块文档

---

**现在就试试吧!** 🎬

```bash
python test_recognition_mac.py
```
