# -*- coding: utf-8 -*-
"""
WALL-E 履带角色动画引擎 (X/Y 模拟量关键帧版)
================================================

结构参考原作者 animations.ino 的声明式关键帧表写法:
动画 = 数据表, 播放器 = 一个通用循环。数据与逻辑分离,
改动画节奏只需改表, 换标定只需改常量。

【实机协议 (2026-08-18 抬轮验证, 与 GitHub 仓库相反!)】
    X±n = 前进/后退 n%     (X100 = 全速前进)
    Y±n = 原地左转/右转 n%  (Y100 = 全速左转, 两轮反向)
    混控: 左履带 = X−Y, 右履带 = X+Y
    停止: X0 + Y0
固件收到目标值后自动做梯形加减速, 无需脉冲模拟降速
(旧 w/q 脉冲方案会引发 UNO 欠压复位并冻死 Pi, 已废弃)。

【标定锚点 (实机实测)】
    Y100 自转 360° = 10s   |   X50+Y86 贴身回转 360° = 19s
所有转弯时长由锚点按角度比例派生; 地面/速度变了只改这两个数。

【帧格式】 (x, y, 保持秒数)  — 每帧双轴全发, 状态幂等,
断线重连后不会残留上一帧的单轴旧目标。
"""
import logging
import time
from threading import Event, Lock, Thread


# ==================== 标定常量 (唯一需要动的地方) ====================

SPIN_360_SECS = 10.0    # Y=±100 原地自转一圈的秒数
PIVOT_360_SECS = 19.0   # X=50,Y=86 贴身回转一圈的秒数


def spin(angle_deg: float) -> float:
    """原地转 angle 度需要的秒数 (由自转标定锚点派生)。"""
    return round(abs(angle_deg) * SPIN_360_SECS / 360.0, 2)


def pivot(angle_deg: float) -> float:
    """贴身回转 angle 度需要的秒数 (由贴身标定锚点派生)。"""
    return round(abs(angle_deg) * PIVOT_360_SECS / 360.0, 2)


# ==================== 速度档位 (X 前后 / Y 转向, 单位 %) ====================

CREEP  = 40    # 最慢直行 (标定验证过 X40 可动; 低于此可能推不动履带)
SLOW   = 60    # 试探/犹豫步
BACK   = 55    # 常规后退
PROBE  = 55    # 好奇探步
CRUISE = 70    # 巡逻巡航
SPRINT = 100   # 冲刺/逃离 (满速)

PAUSE_GAP = 0.3    # 动作间常规停顿(秒), 对应旧版 TURN_GAP
STOP_FRAME = (0, 0, 0.5)   # 收尾安全帧


# ==================== 动画关键帧表 ====================
# 每行 = (x, y, 保持秒数)
# x: -100后退 ~ +100前进 | y: -100右转 ~ +100左转 (原地)

CURIOUS = [
    (CREEP, 0, 2.2),        # 试探性慢蹭接近
    (0, 0, 0.8),            # 停住, 张望
    (PROBE, 0, 0.5),        # 好奇探一步
    (0, 0, 0.3),
    (-BACK, 0, 0.9),        # 又缩回来
    (0, 0, 1.0),
    (0, 100, spin(45)),     # 左看 45°
    (0, 0, PAUSE_GAP),
    (0, -100, spin(90)),    # 右甩 90°
    (0, 0, PAUSE_GAP),
    (0, 100, spin(45)),     # 回中
    (0, 0, 0.8),
    (CREEP, 0, 2.0),        # 犹豫地再蹭一段
    (0, 0, 0.5),
    (SPRINT, 0, 2.2),       # 下定决心, 冲刺!
    STOP_FRAME,
]

STARTLED = [
    (-SPRINT, 0, 1.6),      # 受惊急退
    (0, 0, 0.3),
    (0, 100, spin(45)),     # 慌忙看左边
    (0, 0, PAUSE_GAP),
    (0, -100, spin(225)),   # 猛甩到反方向
    (0, 0, PAUSE_GAP),
    (SPRINT, 0, 3.0),       # 全速逃离
    STOP_FRAME,
]

# 正方形巡逻: 4 x (直行 -> 停下检查 -> 右转90)
_side = [
    (CRUISE, 0, 4.2),       # 沿一条边直行
    (0, 0, 0.25),
    (0, 0, 1.2),            # 到角, 停下压实/检查
    (0, -100, spin(90)),    # 右转 90° 到下一条边
    (0, 0, PAUSE_GAP),
]
PATROL = _side * 4 + [STOP_FRAME]

PUPPY = [
    (CRUISE, 0, 0.6),       # 蹦蹦跳跳走两步
    (0, 0, 0.2),
    (CRUISE, 0, 0.6),
    (0, 0, 0.2),
    (0, -100, spin(360)),   # 开心右转一整圈!
    (0, 0, PAUSE_GAP),
    (0, 100, spin(360)),    # 再左转一整圈!
    (0, 0, PAUSE_GAP),
    (CRUISE, 0, 0.6),       # 又蹦两步
    (0, 0, 0.2),
    (CRUISE, 0, 0.6),
    (0, 0, 0.2),
    (0, -100, spin(360)),   # 再转两圈!
    (0, 0, PAUSE_GAP),
    (0, 100, spin(360)),
    STOP_FRAME,
]

EXCITED = [
    (SPRINT, 0, 0.18),      # 小碎步前抖 (固件斜坡使其成为轻快的小跳,
    (0, 0, 0.1),            #  而非旧版的满扭矩冲击)
    (SPRINT, 0, 0.18),
    (0, 0, 0.1),
    (SPRINT, 0, 0.18),
    (0, 0, 0.1),
    (SPRINT, 0, 0.18),
    (0, 0, 0.1),
    (SPRINT, 0, 0.18),
    (0, 0, 0.1),
    (SPRINT, 0, 0.18),
    (0, 0, 0.1),
    (0, -100, spin(360)),   # 急转一整圈
    (0, 0, PAUSE_GAP),
    (SPRINT, 0, 0.15),      # 再抖一会儿
    (0, 0, 0.08),
    (SPRINT, 0, 0.15),
    (0, 0, 0.08),
    (SPRINT, 0, 0.15),
    (0, 0, 0.08),
    (SPRINT, 0, 0.15),
    (0, 0, 0.08),
    (SPRINT, 0, 2.2),       # 压抑不住, 冲刺!
    STOP_FRAME,
]

HESITANT = [
    (SLOW, 0, 0.75),        # 前进一步
    (0, 0, 0.25),
    (-BACK, 0, 1.3),        # 害怕了, 退两步
    (0, 0, 0.5),
    (SLOW, 0, 0.6),         # 又想试试
    (0, 0, 0.25),
    (-BACK, 0, 0.9),        # 还是怕, 再退
    (0, 0, 0.8),
    (0, 100, spin(45)),     # 左右张望, 纠结
    (0, 0, PAUSE_GAP),
    (0, -100, spin(90)),
    (0, 0, PAUSE_GAP),
    (0, 100, spin(45)),
    (0, 0, 1.0),
    (CREEP, 0, 1.2),        # 终于下定决心, 慢慢起步
    (0, 0, 0.3),
    (SPRINT, 0, 1.5),       # 加速!
    STOP_FRAME,
]

SPIN = [
    (0, -100, spin(360)),   # 右转一整圈
    (0, 0, PAUSE_GAP),
    (0, 100, spin(360)),    # 左转一整圈
    STOP_FRAME,
]

# 标定序列: 依次验证满速直行 / 自转 / 贴身回转 / 死区, 各段应走已知量
CALIBRATE = [
    (100, 0, 2.0),          # 满速直行 2s (量距离, 供直行速度估算)
    (0, 0, 1.0),
    (0, 100, 5.0),          # 自转 5s → 应恰好转 180°
    (0, 0, 1.0),
    (50, 86, pivot(180)),   # 贴身回转 → 应恰好转 180°
    (0, 0, 1.0),
    (CREEP, 0, 3.0),        # X40 慢行: 履带应能动 (死区验证)
    STOP_FRAME,
]

ANIMATIONS = {
    'curious':   CURIOUS,     # 好奇接近 ~17s
    'startled':  STARTLED,    # 受惊逃跑 ~14s
    'patrol':    PATROL,      # 正方形巡逻 ~34s
    'puppy':     PUPPY,       # 小狗式 ~45s
    'excited':   EXCITED,     # 兴奋 ~16s
    'hesitant':  HESITANT,    # 犹豫试探 ~15s
    'spin':      SPIN,        # 原地旋转 ~21s
    'calibrate': CALIBRATE,   # 标定序列 ~24s
}


# ==================== 播放器 ====================

class TrackAnimator:
    """通用关键帧播放器: 逐帧发 X/Y 目标并保持, 安全停机由 finally 兜底。"""

    def __init__(self, arduino_device):
        self._arduino = arduino_device
        self._stop = Event()
        self._lock = Lock()      # 串行化 play/stop 切换, 保证同时最多一个动画线程
        self._thread = None

    # ---------------- 内部工具 ----------------

    @staticmethod
    def _clamp(v):
        return max(-100, min(100, int(v)))

    def _send_xy(self, x, y):
        """双轴全发: 每帧都是完整状态, 不依赖上一帧残留。"""
        self._arduino.send_command(f"X{self._clamp(x)}")
        self._arduino.send_command(f"Y{self._clamp(y)}")

    def _sleep(self, secs):
        """可中断 sleep, 每 20ms 检查停止信号。"""
        end = time.time() + secs
        while time.time() < end:
            if self._stop.is_set():
                return
            time.sleep(min(0.02, max(0, end - time.time())))

    def _halt(self):
        """停掉当前动画线程并清空未发出的旧命令 (调用方须持有 self._lock)。"""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        self._thread = None
        # 丢弃队列里旧动画的残留命令, 防止停止后又漏发出去
        self._arduino.clear_queue()

    # ---------------- 公共 API ----------------

    def play(self, name):
        """播放一段动画; 未知名称或 Arduino 未连接返回 False。"""
        frames = ANIMATIONS.get(name)
        if frames is None or not self._arduino.is_connected():
            return False
        with self._lock:
            self._halt()
            self._stop.clear()
            self._thread = Thread(target=self._run, args=(frames,), daemon=True)
            self._thread.start()
        return True

    def stop(self):
        """停止动画并命令电机停转。"""
        with self._lock:
            self._halt()
            self._send_xy(0, 0)

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    # ---------------- 播放线程 ----------------

    def _run(self, frames):
        try:
            for x, y, duration in frames:
                if self._stop.is_set():
                    return
                self._send_xy(x, y)
                self._sleep(duration)
        except Exception as ex:
            logging.error(f'Track animation error: {repr(ex)}')
        finally:
            # 无论正常结束/异常/中断, 最后必须命令停机:
            # 原版固件无失联保护, 最后一条 X/Y 会一直生效
            self._send_xy(0, 0)
