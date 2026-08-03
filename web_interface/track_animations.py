# -*- coding: utf-8 -*-
"""
WALL-E 履带动画引擎 (Web 版)
================================

这个文件是 walle_drive.py 的 Web 整合版。
区别在于: 不自己开串口, 而是复用 app.py 里 ArduinoDevice 的
持久连接(零启动延迟), 通过 arduino.send_command() 发指令。

每个动画跑在独立后台线程里, 新请求会先停旧的。
线程内的 sleep 是可中断的(每 20ms 检查一次停止信号),
所以用户随时可以切到新动画或紧急停止。

【如何修改动画】
    找到对应的 def xxx(self) 方法, 里面的时间/动作都是直观的。
    self._send('w')   = 发前进
    self._send('q')   = 发停止
    self._burst(ch, secs) = 发指令保持 secs 秒
    self._pulse(ch, dur, on, off) = 脉冲驱动(等效降速)
    self._back(secs) / self._pback(...) = 后退(自动乘 BACK_BIAS 补偿)
    self._sleep(secs) = 可中断的等待
    if self._stop.is_set(): return = 检查停止信号
"""
import time
import random
import logging
from threading import Event, Thread


# ==================== 传动补偿参数 ====================
# 这台机器人前进传动效率高、后退低, 组合动作也有整体损耗。
# 改这里三个数字就能全局调整所有动画。

BACK_BIAS = 1.4    # 后退时长乘数。传动效率低就放大后退时间。
                   #   1.0=不补偿; 1.4=后退慢40%就给40%额外时间。
                   #   实测后退走不远就调大(1.5/1.6)。
GAP = 0.25         # 动作之间的标准过渡间隙(秒)。
                   #   电机刚停有惯性, 间隙太小下个动作会被吃掉。
TURN_GAP = 0.3     # 转弯后额外停顿(秒), 比直线间隙更大。

# --- 转弯基准(秒) ---
# 你的机器人实测: 1.2 秒约转 90 度。所有转弯动画从这里推导。
# 【调法】在网页上点 "Turn Right", 看转了多少度——不到 90 就调大,
#   超过就调小, 一次加减 0.2。调准这一个, spin/figure8/patrol 全对。
TURN_90  = 1.2     # 90 度转弯所需秒数, 实地微调。
TURN_180 = TURN_90 * 2   # 180 度(转身)
TURN_270 = TURN_90 * 3   # 270 度
TURN_360 = TURN_90 * 4   # 360 度(整圈)


# ==================== TrackAnimator 类 ====================

class TrackAnimator:
    """履带动画引擎: 管理线程、发指令、编排动画序列。"""

    def __init__(self, arduino_device):
        # arduino_device 是 app.py 里的 ArduinoDevice 实例
        self._arduino = arduino_device
        self._stop = Event()       # 停止信号
        self._thread = None        # 当前动画线程引用

    # ---------------- 基础原语 ----------------

    def _send(self, ch):
        """发一条单字符指令到 arduino 命令队列(非阻塞)。"""
        self._arduino.send_command(ch)

    def _sleep(self, secs):
        """可中断的 sleep, 每 20ms 检查停止信号。"""
        end = time.time() + secs
        while time.time() < end:
            if self._stop.is_set():
                return
            time.sleep(min(0.02, max(0, end - time.time())))

    def _burst(self, ch, secs):
        """发指令并保持 secs 秒(持续通电)。"""
        self._send(ch)
        self._sleep(secs)

    def _pulse(self, ch, duration, on=0.18, off=0.12):
        """脉冲驱动: 通电 on 秒 -> 停 off 秒, 等效降速。

        固件的 w/s/a/d 是满速 255, 没法直接调速。
        这里用间歇通电模拟慢速。on 越小/off 越大 = 越慢。
        """
        end = time.time() + duration
        while time.time() < end and not self._stop.is_set():
            self._send(ch)
            self._sleep(on)
            self._send('q')
            self._sleep(off)

    def _back(self, secs):
        """后退(持续通电), 自动乘 BACK_BIAS 补偿传动效率差。"""
        self._burst('s', secs * BACK_BIAS)

    def _pback(self, duration, on=0.18, off=0.12):
        """脉冲后退(等效慢速), 自动乘 BACK_BIAS 补偿。"""
        self._pulse('s', duration * BACK_BIAS, on, off)

    # ---------------- 动画函数 ----------------
    # 每个 for 循环开头都检查 self._stop, 被停止时尽快退出。

    def test(self):
        """前进 2 秒。链路自检。"""
        self._burst('w', 2.0); self._send('q')

    def wiggle(self):
        """左右摇摆 4 次, 逐渐加快。"""
        for t in [0.5, 0.4, 0.3, 0.2]:
            if self._stop.is_set(): return
            self._burst('a', t); self._send('q'); self._sleep(0.15)
            self._burst('d', t * 2); self._send('q'); self._sleep(0.15)
            self._burst('a', t); self._send('q'); self._sleep(0.15)

    def scan(self):
        """原地慢速扫描: 左转 -> 停 -> 右转 -> 停 -> 回中。"""
        self._pulse('a', 2.5); self._send('q'); self._sleep(TURN_GAP)
        self._pulse('d', 5.0); self._send('q'); self._sleep(TURN_GAP)
        self._pulse('a', 2.5); self._send('q')

    def patrol(self):
        """方形巡逻: 前进 + 右转 90, 重复 4 次画方形。"""
        for _ in range(4):
            if self._stop.is_set(): return
            self._burst('w', 1.5); self._send('q'); self._sleep(GAP)
            self._burst('d', TURN_90); self._send('q'); self._sleep(TURN_GAP)

    def happy(self):
        """兴奋: 冲刺 + 加速摇摆 + 原地旋转。"""
        self._burst('w', 0.5); self._send('q'); self._sleep(GAP)
        for t in [0.2, 0.15, 0.1]:
            if self._stop.is_set(): return
            self._burst('a', t); self._send('q')
            self._burst('d', t * 2); self._send('q')
            self._burst('a', t); self._send('q')
        self._sleep(0.1)
        self._burst('d', TURN_360); self._send('q')   # 旋转一整圈

    def backup(self):
        """后退 + 转身离开。"""
        self._back(0.8); self._send('q'); self._sleep(GAP)
        self._burst('d', TURN_180); self._send('q'); self._sleep(TURN_GAP)
        self._burst('w', 0.8); self._send('q')

    def dance(self):
        """舞步: 前后左右交替, 3 轮节奏递快。"""
        for t in [0.5, 0.35, 0.25]:
            if self._stop.is_set(): return
            self._burst('w', t); self._send('q'); self._sleep(0.12)
            self._burst('d', t * (TURN_90 / 0.8)); self._send('q'); self._sleep(0.12)
            self._back(t); self._send('q'); self._sleep(0.12)
            self._burst('a', t * (TURN_90 / 0.8)); self._send('q'); self._sleep(0.12)
            self._sleep(0.1)

    def figure8(self):
        """画 8 字: 右转一圈 + 前进 + 左转一圈 + 前进。"""
        self._burst('w', 0.6); self._send('q'); self._sleep(GAP)
        self._burst('d', TURN_360); self._send('q'); self._sleep(TURN_GAP)
        self._burst('w', 0.8); self._send('q'); self._sleep(GAP)
        self._burst('a', TURN_360); self._send('q'); self._sleep(TURN_GAP)
        self._burst('w', 0.6); self._send('q')

    def zigzag(self):
        """蛇形前进: 左转前进右转前进, 重复 4 段。"""
        for _ in range(4):
            if self._stop.is_set(): return
            self._burst('a', TURN_90 * 0.4); self._send('q'); self._sleep(0.15)
            self._burst('w', 0.8); self._send('q'); self._sleep(0.15)
            self._burst('d', TURN_90 * 0.8); self._send('q'); self._sleep(0.15)
            self._burst('w', 0.8); self._send('q'); self._sleep(0.15)

    def celebrate(self):
        """庆祝: 冲刺 -> 旋转两周 -> 摇摆 -> 胜利前进。"""
        self._burst('w', 0.4); self._send('q'); self._sleep(GAP)
        self._burst('d', TURN_360); self._send('q'); self._sleep(TURN_GAP)
        self._burst('a', TURN_360); self._send('q'); self._sleep(TURN_GAP)
        self.wiggle()
        self._sleep(GAP)
        self._burst('w', 1.0); self._send('q'); self._sleep(GAP)
        self._burst('d', TURN_180); self._send('q'); self._sleep(TURN_GAP)
        self._burst('w', 0.8); self._send('q')

    def wander(self):
        """随机漫游 3 轮, 像在探索。"""
        moves = ['w', 's', 'a', 'd']
        for _ in range(3):
            if self._stop.is_set(): return
            m = random.choice(moves)
            dur = random.uniform(0.4, 1.5)
            if m == 's':
                self._back(dur); self._send('q')
            else:
                self._burst(m, dur); self._send('q')
            self._sleep(random.uniform(0.2, 0.45))

    def spin(self):
        """原地旋转: 右转一整圈再左转一整圈。"""
        self._burst('d', TURN_360); self._send('q'); self._sleep(TURN_GAP)
        self._burst('a', TURN_360); self._send('q'); self._sleep(TURN_GAP)
        self._burst('d', TURN_360); self._send('q'); self._sleep(TURN_GAP)
        self._burst('a', TURN_360); self._send('q')

    def spin8(self):
        """连续旋转: 同方向转 8 圈(4 右 + 4 左)。"""
        for _ in range(4):
            if self._stop.is_set(): return
            self._burst('d', TURN_360); self._send('q'); self._sleep(TURN_GAP)
        for _ in range(4):
            if self._stop.is_set(): return
            self._burst('a', TURN_360); self._send('q'); self._sleep(TURN_GAP)

    def waltz(self):
        """华尔兹三拍: 前进-右转-左转, 优雅慢速, 4 小节。"""
        for _ in range(4):
            if self._stop.is_set(): return
            self._pulse('w', 0.7, on=0.25, off=0.08); self._send('q'); self._sleep(GAP)
            self._pulse('d', 0.7, on=0.25, off=0.08); self._send('q'); self._sleep(TURN_GAP)
            self._pulse('a', 0.7, on=0.25, off=0.08); self._send('q'); self._sleep(TURN_GAP)

    def explore(self):
        """探索模式: 前进 -> 停下张望(左右转) -> 随机换方向。"""
        for _ in range(3):
            if self._stop.is_set(): return
            self._pulse('w', 1.5, on=0.2, off=0.1); self._send('q'); self._sleep(GAP)
            self._burst('a', TURN_90 * 0.5); self._send('q'); self._sleep(TURN_GAP)
            self._burst('d', TURN_90); self._send('q'); self._sleep(TURN_GAP)
            self._burst('a', TURN_90 * 0.5); self._send('q'); self._sleep(GAP)
            turn = random.choice(['a', 'd'])
            self._burst(turn, random.uniform(TURN_90 * 0.25, TURN_90 * 0.7)); self._send('q'); self._sleep(TURN_GAP)

    def panic(self):
        """慌张: 快速短促不规则运动, 像受惊乱跑。"""
        for _ in range(8):
            if self._stop.is_set(): return
            m = random.choice(['w', 's', 'a', 'd'])
            dur = random.uniform(0.1, 0.35)
            if m == 's':
                self._back(dur); self._send('q')
            else:
                self._burst(m, dur); self._send('q')
            self._sleep(random.uniform(0.05, 0.12))

    def tease(self):
        """调皮试探: 前进一点 -> 退回 -> 再多一点 -> 退回 -> 冲出。"""
        for d in [0.3, 0.5, 0.7]:
            if self._stop.is_set(): return
            self._pulse('w', d, on=0.15, off=0.1); self._send('q'); self._sleep(GAP)
            self._back(d * 0.8); self._send('q'); self._sleep(0.35)
        self._burst('w', 1.0); self._send('q')

    def orbit(self):
        """圆弧轨迹: 前进的同时持续微转, 画一个大圆。"""
        for _ in range(8):
            if self._stop.is_set(): return
            self._burst('w', 0.5); self._send('q'); self._sleep(0.08)
            self._burst('d', 0.3); self._send('q'); self._sleep(0.08)

    def slinky(self):
        """弹簧步: 超慢一蹭一蹭前进, 像弹簧玩具。"""
        self._pulse('w', 4.0, on=0.12, off=0.18); self._send('q')

    def bow(self):
        """鞠躬: 后退一步 -> 停顿 -> 前进致敬 -> 摇摆。"""
        self._pback(0.8, on=0.15, off=0.08); self._send('q'); self._sleep(0.5)
        self._burst('w', 0.7); self._send('q'); self._sleep(GAP)
        self.wiggle()

    def moonwalk(self):
        """太空步: 后退为主, 中间穿插小幅转向。"""
        for _ in range(3):
            if self._stop.is_set(): return
            self._pback(0.7, on=0.2, off=0.1); self._send('q'); self._sleep(0.1)
            self._burst('a', 0.2); self._send('q'); self._sleep(0.1)
            self._pback(0.7, on=0.2, off=0.1); self._send('q'); self._sleep(0.1)
            self._burst('d', 0.2); self._send('q'); self._sleep(0.1)

    def showcase(self):
        """表演集锦: 把主要动画串成一场完整演出。"""
        lineup = [
            self.bow, self.waltz, self.zigzag, self.celebrate,
            self.tease, self.scan, self.slinky, self.panic,
            self.moonwalk, self.wiggle,
        ]
        for fn in lineup:
            if self._stop.is_set(): return
            fn()
            self._sleep(0.6)

    # ---------------- 基础移动(带默认时长) ----------------

    def forward(self):
        self._burst('w', 1.0); self._send('q')

    def backward(self):
        self._back(1.0); self._send('q')

    def left(self):
        self._burst('a', TURN_90); self._send('q')

    def right(self):
        self._burst('d', TURN_90); self._send('q')

    # ---------------- 公共 API ----------------

    # 名称 -> 方法名 的映射。加新动画: 写完 def 后在这里加一行。
    ACTIONS = {
        'forward':  'forward',
        'backward': 'backward',
        'left':     'left',
        'right':    'right',
        'test':     'test',
        'scan':     'scan',
        'patrol':   'patrol',
        'orbit':    'orbit',
        'zigzag':   'zigzag',
        'figure8':  'figure8',
        'wiggle':   'wiggle',
        'happy':    'happy',
        'dance':    'dance',
        'waltz':    'waltz',
        'celebrate':'celebrate',
        'spin':     'spin',
        'spin8':    'spin8',
        'explore':  'explore',
        'wander':   'wander',
        'tease':    'tease',
        'panic':    'panic',
        'slinky':   'slinky',
        'bow':      'bow',
        'moonwalk': 'moonwalk',
        'backup':   'backup',
        'showcase': 'showcase',
    }

    def play(self, name):
        """停止当前动画(如果有), 启动新动画。

        :param name: ACTIONS 字典里的键名
        :return: True 如果动画已启动, False 如果名称无效或 Arduino 未连接
        """
        method_name = self.ACTIONS.get(name)
        if method_name is None:
            return False
        if not self._arduino.is_connected():
            return False

        # 先停掉正在跑的动画
        self.stop()
        self._stop.clear()

        # 起后台线程跑动画
        self._thread = Thread(target=self._run, args=(method_name,), daemon=True)
        self._thread.start()
        return True

    def stop(self):
        """停止当前动画, 确保电机停转。"""
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        self._send('q')

    def _run(self, method_name):
        """动画线程入口: 跑完后确保发 q 停车。"""
        try:
            getattr(self, method_name)()
        except Exception as ex:
            logging.error(f'Track animation error [{method_name}]: {repr(ex)}')
        finally:
            self._send('q')

    def is_running(self):
        """是否有动画正在跑。"""
        return self._thread is not None and self._thread.is_alive()
