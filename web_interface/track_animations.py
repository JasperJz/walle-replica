# -*- coding: utf-8 -*-
"""
WALL-E 履带角色动画引擎 (X/Y 模拟量关键帧版)
================================================

结构参考原作者 animations.ino 的声明式关键帧表写法:
动画 = 数据表, 播放器 = 一个通用循环。数据与逻辑分离,
改动画节奏只需改表, 换标定只需改常量。

【实机协议 (2026-08-18 抬轮验证; 成因: upstream 固件 + 左电机反接)】
    X±n = 前进/后退 n%     (X100 = 全速前进)
    Y±n = 原地左转/右转 n%  (Y100 = 全速左转, 两轮反向)
    物理混控: 左履带 = X−Y, 右履带 = X+Y
    停止: X0 + Y0
固件收到目标值后自动做梯形加减速(0→满速约 0.32s, 加/减速段
在"运动帧+停顿帧"中近似抵消, 故角度 = 满速角速度 × 保持时长)。

【标定锚点 (2026-08-18 实测, 口径: 命令发出→停稳, 含加减速尾巴)】
    Y100 自转 360° = 10.0s   |   X50+Y86 贴身回转 360° = 19.0s
所有转弯时长由锚点按角度线性派生; 换地面/速度变了先跑
CALIBRATE 验证, 修这两个数即可全表重标。

【帧格式】 (x, y, 保持秒数)  — 每帧双轴全发, 状态幂等。

【速度档位 = 性格词汇表】 单调递增:
    creep(40) < hesitant(48) < probe(55) < cruise(70) < sprint(100)
    冲刺是稀缺词汇, 只属于 startled 的逃亡与 excited 的高潮。
"""
import logging
import random
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
SLOW   = 48    # 犹豫/试探步 (必须 < PROBE: 胆子越小步子越慢)
BACK   = 55    # 常规后退
PROBE  = 55    # 好奇探步
CRUISE = 70    # 巡逻巡航 / 下定决心
SPRINT = 100   # 冲刺/逃离 (满速, 稀缺词汇)

PAUSE_GAP = 0.3     # 动作间常规停顿(秒)
SPRINT_GAP = 0.45   # 满速后的停顿要更长 (0.3s 斜坡还没停稳)
STOP_FRAME = (0, 0, 0.5)   # 收尾安全帧

# 重放微变异开关: 研究场景反复暴露同一批动画时, 逐比特相同的重放
# 读作"录音机"。开启后每帧时长 ±8% 随机扰动, 让节拍有"活性"。
# 标定序列 CALIBRATE 永远确定性。
VARY = False


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
    (PROBE, 0, 0.8),        # 鼓起勇气, 放大胆子靠近
    (0, 0, 0.8),            # 停下端详 — 好奇的终点是"看清", 不是逃跑
    STOP_FRAME,
]

STARTLED = [
    (0, 0, 0.25),           # 僵住! (受惊先冻结, 不是直接跑)
    (-SPRINT, 0, 0.4),      # 短促一缩
    (0, 0, 0.2),
    (-SPRINT, 0, 1.2),      # 急退
    (0, 0, PAUSE_GAP),
    (0, 100, spin(45)),     # 慌忙看左边
    (0, 0, 0.15),
    (0, -100, spin(45)),    # 猛回头 (double-take)
    (0, 0, 0.15),
    (0, -100, spin(180)),   # 掉头, 面向反方向
    (0, 0, PAUSE_GAP),
    (SPRINT, 0, 2.2),       # 全速逃离 (冲刺只属于恐惧与狂喜)
    (0, 0, SPRINT_GAP),
    STOP_FRAME,
]

# 正方形巡逻: 4 x (直行 -> 停下检查 -> 右转90)
_side = [
    (CRUISE, 0, 4.2),       # 沿一条边直行
    (0, 0, 1.45),           # 到角, 停下压实/检查
    (0, -100, spin(90)),    # 右转 90° 到下一条边
    (0, 0, PAUSE_GAP),
]
PATROL = _side * 4 + [STOP_FRAME]

PUPPY = [
    (CRUISE, 0, 0.6),       # 蹦蹦跳跳走两步
    (0, 0, 0.2),
    (CRUISE, 0, 0.6),
    (0, 0, 0.2),
    (0, -100, spin(360)),   # 开心右转一整圈! (唯一的原地满圈, 高潮)
    (0, 0, PAUSE_GAP),
    (CRUISE, 0, 0.6),       # 又蹦两步
    (0, 0, 0.2),
    (CRUISE, 0, 0.6),
    (0, 0, 0.2),
    (50, 86, pivot(180)),   # 贴身大弧线绕到背面 (换个姿态转, 不再陀螺)
    (0, 0, PAUSE_GAP),
    (SPRINT, 0, 0.6),       # 开心冲刺一小段!
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
    (0, 0, SPRINT_GAP),
    STOP_FRAME,
]

HESITANT = [
    (SLOW, 0, 0.75),        # 前进一小步
    (0, 0, 0.25),
    (-BACK, 0, 0.65),       # 害怕了, 退两步 (真的是两步)
    (0, 0, 0.15),
    (-BACK, 0, 0.65),
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
    (CRUISE, 0, 1.8),       # 稳步走 — 战胜恐惧不等于狂奔
    STOP_FRAME,
]

SPIN = [
    (0, -100, spin(360)),   # 右转一整圈
    (0, 0, PAUSE_GAP),
    (0, 100, spin(360)),    # 左转一整圈
    STOP_FRAME,
]

# 标定序列: 依次验证直行/自转/贴身/90°线性度/死区, 各段应走已知量。
# 小角度 90° 段用于验证线性派生是否有系统性偏差 (>2° 同号偏差才需立项补偿)。
CALIBRATE = [
    (100, 0, 2.0),          # 满速直行 2s (看走线是否直线, 量距离)
    (0, 0, 1.0),
    (0, 100, spin(180)),    # 自转 180°
    (0, 0, 1.0),
    (0, 100, spin(90)),     # 自转 90° (线性度验证)
    (0, 0, 1.0),
    (50, 86, pivot(180)),   # 贴身回转 180°
    (0, 0, 1.0),
    (CREEP, 0, 3.0),        # X40 慢行: 履带应能动 (死区验证)
    STOP_FRAME,
]

ANIMATIONS = {
    'curious':   CURIOUS,
    'startled':  STARTLED,
    'patrol':    PATROL,
    'puppy':     PUPPY,
    'excited':   EXCITED,
    'hesitant':  HESITANT,
    'spin':      SPIN,
    'calibrate': CALIBRATE,
}

# 网页显示名 (时长由表实时求和, 不在此处复制数字)
DISPLAY = {
    'curious':   'Curious Approach',
    'startled':  'Startled Escape',
    'patrol':    'Square Patrol',
    'puppy':     'Puppy Play',
    'excited':   'Excited',
    'hesitant':  'Hesitant',
    'spin':      'Full Spin',
    'calibrate': 'Calibration Run',
}


def animation_length(name):
    """某段动画的总时长(秒) — UI 进度条的唯一数据源。"""
    return round(sum(f[2] for f in ANIMATIONS[name]), 1)


# ==================== 播放器 ====================

class TrackAnimator:
    """通用关键帧播放器: 逐帧发 X/Y 目标并保持, 安全停机由 finally 兜底。"""

    def __init__(self, arduino_device):
        self._arduino = arduino_device
        self._lock = Lock()       # 串行化 play/stop 切换, 保证同时最多一个动画线程
        self._thread = None
        self._stop = None         # 每次播放独立 Event: 旧播放的停止信号
                                   # 不会被下一次 play 的 clear() 复活
    # ---------------- 内部工具 ----------------

    @staticmethod
    def _clamp(v):
        return max(-100, min(100, int(v)))

    def _send_xy(self, x, y):
        """双轴全发: 每帧都是完整状态, 不依赖上一帧残留。"""
        ok1 = self._arduino.send_command(f"X{self._clamp(x)}")
        ok2 = self._arduino.send_command(f"Y{self._clamp(y)}")
        return ok1 and ok2

    @staticmethod
    def _sleep(stop_event, secs):
        """可中断 sleep, 每 20ms 检查停止信号。"""
        end = time.time() + secs
        while time.time() < end:
            if stop_event.is_set():
                return
            time.sleep(min(0.02, max(0, end - time.time())))

    def _halt(self):
        """停掉当前动画线程并清空未发出的旧命令 (调用方须持有 self._lock)。"""
        if self._stop is not None:
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
            if self._thread is not None and self._thread.is_alive():
                logging.info(f'Track animation [{name}] interrupts running animation')
            self._halt()
            stop_event = Event()
            self._stop = stop_event
            self._thread = Thread(target=self._run, args=(frames, name, stop_event), daemon=True)
            self._thread.start()
        logging.info(f'Track animation started [{name}] ({animation_length(name)}s)')
        return True

    def stop(self):
        """停止动画并命令电机停转。"""
        with self._lock:
            was_running = self.is_running()
            self._halt()
            self._send_stop()
        if was_running:
            logging.info('Track animation stopped')

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    # ---------------- 播放线程 ----------------

    def _run(self, frames, name, stop_event):
        fail_streak = 0
        try:
            for x, y, duration in frames:
                if stop_event.is_set():
                    return
                if VARY and name != 'calibrate' and duration >= 0.2:
                    duration *= random.uniform(0.92, 1.08)
                if not self._send_xy(x, y):
                    # 发送失败 = 串口断开/重连中; 连续多帧失败说明链路已不可用,
                    # 从表格中段复活只会呈现残缺节拍, 不如体面中止
                    fail_streak += 1
                    if fail_streak >= 3:
                        logging.warning(f'Track animation [{name}] aborted: serial link lost')
                        return
                else:
                    fail_streak = 0
                self._sleep(stop_event, duration)
        except Exception as ex:
            logging.error(f'Track animation error [{name}]: {repr(ex)}')
        finally:
            # 无论正常结束/异常/中断, 最后必须命令停机:
            # 原版固件无失联保护, 最后一条 X/Y 会一直生效
            self._send_stop()

    def _send_stop(self, attempts=5, interval=0.1):
        """停机帧带有限重试: 断线时 send_command 返回 False,
        短暂掉线窗口内重试补发; 更长的掉线由串口重连路径的
        clear_queue + X0/Y0 兜底 (见 app.py __reconnect_serial)。"""
        for _ in range(attempts):
            if self._arduino.send_command("X0") and self._arduino.send_command("Y0"):
                return
            time.sleep(interval)
