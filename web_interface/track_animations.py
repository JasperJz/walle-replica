# -*- coding: utf-8 -*-
"""
WALL-E 履带动画引擎 (Web 版)
================================

7 段角色化动画, 每段 10-25 秒, 用节奏差异表达 WALL-E 的性格。
不靠运动种类(只有直行/后退/自转), 靠停顿、加速、犹豫来制造角色感。

【7 段动画】
    1. curious   — 好奇接近 (慢蹭/停/缩回/张望/冲刺)
    2. startled  — 受惊逃跑 (急退/慌张找方向/全速逃离)
    3. patrol    — 正方形巡逻 (走/转/走/转/回原位)
    4. puppy     — 小狗式 (走两步/开心转圈/再走/再转)
    5. excited   — 兴奋 (碎步抖动/急转/再抖/冲刺)
    6. hesitant  — 犹豫试探 (进退纠结/左右张望/最终决定)
    7. spin      — 原地旋转 (右转一圈/左转一圈)

【调参】
    TURN_90 是所有转弯的基准。网页点 "Turn Right", 不到 90 度就调大。
    MOVE_BIAS 补偿履带空转。BACK_BIAS 补偿后退传动效率差。
"""
import time
import random
import logging
from threading import Event, Thread


# ==================== 传动补偿参数 ====================
BACK_BIAS = 1.4    # 后退时长乘数。后退走不远就调大(1.5/1.6)。
MOVE_BIAS = 1.5    # 前进时长全局倍率。履带空转严重就调大(1.6/1.8)。
GAP = 0.25         # 动作之间过渡间隙(秒)。
TURN_GAP = 0.3     # 转弯后额外停顿(秒)。

# --- 转弯基准(秒) — 调这一个数字, 所有动画转弯幅度自动按比例变 ---
TURN_90  = 1.8
TURN_45  = TURN_90 * 0.5
TURN_180 = TURN_90 * 2
TURN_225 = TURN_90 * 2.5
TURN_360 = TURN_90 * 4


# ==================== TrackAnimator 类 ====================

class TrackAnimator:

    def __init__(self, arduino_device):
        self._arduino = arduino_device
        self._stop = Event()
        self._thread = None

    # ---------------- 基础原语 ----------------

    def _send(self, ch):
        """发一条单字符指令到 arduino 命令队列。"""
        self._arduino.send_command(ch)

    def _sleep(self, secs):
        """可中断的 sleep, 每 20ms 检查停止信号。"""
        end = time.time() + secs
        while time.time() < end:
            if self._stop.is_set():
                return
            time.sleep(min(0.02, max(0, end - time.time())))

    def _burst(self, ch, secs):
        """发指令并保持 secs 秒。前进(w)自动乘 MOVE_BIAS 补偿空转。"""
        if ch == 'w':
            secs *= MOVE_BIAS
        self._send(ch)
        self._sleep(secs)

    def _pulse(self, ch, duration, on=0.18, off=0.12):
        """脉冲驱动: 通电 on 秒/停 off 秒, 等效降速。"""
        if ch == 'w':
            duration *= MOVE_BIAS
        end = time.time() + duration
        while time.time() < end and not self._stop.is_set():
            self._send(ch)
            self._sleep(on)
            self._send('q')
            self._sleep(off)

    def _back(self, secs):
        """后退(持续通电), 自动乘 BACK_BIAS。"""
        self._burst('s', secs * BACK_BIAS)

    # ---------------- 7 段角色化动画 ----------------

    def curious(self):
        """好奇接近 (~18s): 慢蹭 -> 停下张望 -> 缩回 -> 犹豫 -> 突然冲刺"""
        # 第一段: 试探性慢蹭
        self._pulse('w', 1.5, on=0.2, off=0.3)
        self._send('q'); self._sleep(0.8)
        # 好奇地探一下又缩回来
        self._burst('w', 0.3); self._send('q'); self._sleep(0.3)
        self._back(0.5); self._send('q'); self._sleep(1.0)
        # 左右张望, 像在判断方向
        self._burst('a', TURN_45); self._send('q'); self._sleep(TURN_GAP)
        self._burst('d', TURN_90); self._send('q'); self._sleep(TURN_GAP)
        self._burst('a', TURN_45); self._send('q'); self._sleep(0.8)
        # 犹豫地再蹭一段
        self._pulse('w', 1.5, on=0.25, off=0.15)
        self._send('q'); self._sleep(0.5)
        # 下定决心, 冲刺!
        self._burst('w', 1.5); self._send('q')

    def startled(self):
        """受惊逃跑 (~12s): 急退 -> 左转45看一眼 -> 右转225到反方向 -> 全速冲"""
        # 受惊急退
        self._back(1.2); self._send('q'); self._sleep(0.3)
        # 慌忙看左边(45度)
        self._burst('a', TURN_45); self._send('q'); self._sleep(TURN_GAP)
        # 猛甩到反方向(225度右转)
        self._burst('d', TURN_225); self._send('q'); self._sleep(TURN_GAP)
        # 全速逃离
        self._burst('w', 2.0); self._send('q')

    def patrol(self):
        """正方形巡逻 (~25s): 直走 -> 停下检查 -> 转90 -> 重复4次回原位"""
        for _ in range(4):
            if self._stop.is_set(): return
            # 沿一条边直行
            self._burst('w', 2.0); self._send('q'); self._sleep(GAP)
            # 到角了, 停下像在压实/检查
            self._sleep(1.2)
            # 右转90度到下一条边
            if self._stop.is_set(): return
            self._burst('d', TURN_90); self._send('q'); self._sleep(TURN_GAP)

    def puppy(self):
        """小狗式 (~25s): 蹦两步 -> 开心转两整圈 -> 再蹦两步 -> 再转两圈"""
        # 蹦蹦跳跳走两步
        for _ in range(2):
            if self._stop.is_set(): return
            self._burst('w', 0.4); self._send('q'); self._sleep(0.2)
        # 开心转两整圈!
        self._burst('d', TURN_360); self._send('q'); self._sleep(TURN_GAP)
        self._burst('a', TURN_360); self._send('q'); self._sleep(TURN_GAP)
        # 又蹦两步
        for _ in range(2):
            if self._stop.is_set(): return
            self._burst('w', 0.4); self._send('q'); self._sleep(0.2)
        # 再转两圈!
        self._burst('d', TURN_360); self._send('q'); self._sleep(TURN_GAP)
        self._burst('a', TURN_360); self._send('q')

    def excited(self):
        """兴奋 (~14s): 碎步抖动 -> 急转一圈 -> 再抖 -> 冲刺"""
        # 小碎步前抖(像激动得控制不住)
        for _ in range(6):
            if self._stop.is_set(): return
            self._burst('w', 0.15); self._send('q'); self._sleep(0.08)
        # 急转一整圈
        self._burst('d', TURN_360); self._send('q'); self._sleep(TURN_GAP)
        # 再抖一会儿
        for _ in range(4):
            if self._stop.is_set(): return
            self._burst('w', 0.12); self._send('q'); self._sleep(0.06)
        # 压抑不住, 冲刺!
        self._burst('w', 1.5); self._send('q')

    def hesitant(self):
        """犹豫试探 (~18s): 进一步退两步 -> 左右张望 -> 最终下定决心"""
        # 前进一步
        self._burst('w', 0.5); self._send('q'); self._sleep(GAP)
        # 害怕了, 退两步
        self._back(1.0); self._send('q'); self._sleep(0.5)
        # 又想试试, 前进
        self._burst('w', 0.4); self._send('q'); self._sleep(GAP)
        # 还是怕, 再退
        self._back(0.7); self._send('q'); self._sleep(0.8)
        # 左右张望, 纠结
        self._burst('a', TURN_45); self._send('q'); self._sleep(TURN_GAP)
        self._burst('d', TURN_90); self._send('q'); self._sleep(TURN_GAP)
        self._burst('a', TURN_45); self._send('q'); self._sleep(1.0)
        # 终于下定决心, 慢慢起步
        self._pulse('w', 1.0, on=0.2, off=0.1)
        self._send('q'); self._sleep(0.3)
        # 加速!
        self._burst('w', 1.0); self._send('q')

    def spin(self):
        """原地旋转 (~15s): 右转一整圈再左转一整圈"""
        self._burst('d', TURN_360); self._send('q'); self._sleep(TURN_GAP)
        self._burst('a', TURN_360); self._send('q')

    # ---------------- 基础移动(测试用) ----------------

    def forward(self):
        self._burst('w', 1.5); self._send('q')

    def backward(self):
        self._back(1.0); self._send('q')

    def left(self):
        self._burst('a', TURN_90); self._send('q')

    def right(self):
        self._burst('d', TURN_90); self._send('q')

    def test(self):
        self._burst('w', 3.0); self._send('q')

    # ---------------- 公共 API ----------------

    ACTIONS = {
        'forward':  'forward',
        'backward': 'backward',
        'left':     'left',
        'right':    'right',
        'test':     'test',
        'curious':  'curious',
        'startled': 'startled',
        'patrol':   'patrol',
        'puppy':    'puppy',
        'excited':  'excited',
        'hesitant': 'hesitant',
        'spin':     'spin',
    }

    def play(self, name):
        method_name = self.ACTIONS.get(name)
        if method_name is None:
            return False
        if not self._arduino.is_connected():
            return False
        self.stop()
        self._stop.clear()
        self._thread = Thread(target=self._run, args=(method_name,), daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        self._send('q')

    def _run(self, method_name):
        try:
            getattr(self, method_name)()
        except Exception as ex:
            logging.error(f'Track animation error [{method_name}]: {repr(ex)}')
        finally:
            self._send('q')

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()
