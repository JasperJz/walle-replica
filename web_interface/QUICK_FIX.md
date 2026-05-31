# 🎯 MacBook 摄像头快速修复

你之前遇到的摄像头立即关闭问题已经修复了！

## Step 1: 安装依赖

```bash
cd web_interface
pip install opencv-python mediapipe
```

## Step 2: 检查摄像头权限

在 Mac 上允许 Python 访问摄像头：
1. 系统偏好设置 → 安全与隐私 → 摄像头
2. 确保 Python/VS Code 在允许列表中

## Step 3: 运行测试

```bash
python test_recognition_mac.py
```

**期望看到:**
- 摄像头窗口显示
- 左上角显示帧数和 FPS
- 状态: "● RUNNING"

**控制:**
- `q` - 退出
- `s` - 统计信息

## 常见问题

**Q: 还是有问题？**

A: 检查以下：
1. 关闭其他使用摄像头的应用
2. 检查 System Preferences → Security & Privacy → Camera 权限
3. 重启 Mac

**Q: MediaPipe 错误？**

A: 运行：
```bash
pip install mediapipe==0.10.9
```

## 更多帮助

- 📖 `GETTING_STARTED.md` - 中文快速指南
- 📖 `recognition/README.md` - 模块详解
