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

【运动范围保证 (2026-08-27 归零轮, 90×180cm 演示桌约束)】
    每张表净朝向 = 0 (±5° 内): 无论单独播放还是按 UI 顺序连播/循环,
    朝向永不累积漂移, 位移只发生在可预期的锥形范围内。
    补偿帧: curious 末瞥+50° / startled 回头+180° / eager 扫视 35→90° /
    discover 回望-75° / shy 不动 (+5° 与 discover -5° 对冲)。
    整轮净位移向量见 docs/track-animations.md 归零轮章节。

【标定锚点 (2026-08-18 实测, 口径: 命令发出→停稳, 含加减速尾巴)】
    Y100 自转 360° = 10.0s   |   X50+Y86 贴身回转 360° = 19.0s
所有转弯时长由锚点按角度线性派生; 换地面/速度变了先跑
CALIBRATE 验证, 修这两个数即可全表重标。

【帧格式】 (x, y, 保持秒数)  — 每帧双轴全发, 状态幂等。

【速度档位 = 性格词汇表】 单调递增:
    drift(35) < creep(40) < slow(48) < probe(55) < cruise(70) < sprint(100)
    冲刺用于 startled 的回缩离场、eager/burst 的爆发帧, 以及 wiggle 的
    节奏乐器(2026-08-25 起wiggle以全速冲刺为节拍主体, 热学注见表头)。
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

DRIFT  = 35    # 最慢直行档, SOLITARY/HOME 的低能量感与 DISCOVER 的端详逼近
               # 2026-08-24 pilot: X30 已实机验证可动, 30→35 增大位移可见度;
               # 夹在两个已验证档 30/40 之间, 无死区风险
CREEP  = 40    # 最慢直行 (标定验证过 X40 可动)
SLOW   = 48    # 犹豫/试探步
BACK   = 55    # 常规后退
PROBE  = 55    # 好奇探步
CRUISE = 70    # 下定决心后的短暂坚定前行
SPRINT = 100   # 冲刺 — startled 的回缩离场、eager/burst 的爆发帧、wiggle 的节奏主体

PAUSE_GAP  = 0.35
SPRINT_GAP = 0.45
STOP_FRAME = (0, 0, 0.5)

VARY = False   # 三个外观条件下必须使用完全相同的行为序列, 正式实验素材保持关闭


# ==================== 角色行为层 (Character-specific behaviours) ====================
# 设计原则: 每条对应WALL-E电影里的具体行为原型, 不追求情绪维度间的正交/独立;
# 唯一的硬约束是: 同一套动画必须在A/B/C三个外观条件下逐帧一致地复用,
# 履带表达力不足是controlled limitation, 但"不同条件用不同动画"才是真正的confound。
# 每行 = (x, y, 保持秒数)
# x: -100后退 ~ +100前进 | y: -100右转 ~ +100左转 (原地)

CURIOUS = [
    # 原型: 检视垃圾堆/陌生物件 — 多地点巡视, 单向趋近, 不含后退
    # 2026-08-25 重构: 与DISCOVER分工 — 本行为位移支配(走-扫视-换地点-再扫视),
    # DISCOVER旋转支配(单物件环绕端详); 行走段为位移主体(每段 ≥1.2 v100·s,
    # 高于 EAGER 结尾冲刺的可见性判据), 扫视是节奏点不是主体
    (CREEP, 0, 2.2),        # 走近第一个地点
    (0, 0, 0.5),
    (0, 100, spin(45)),     # 扫视: 左 45°
    (0, 0, 0.5),
    (0, -100, spin(90)),    # 右扫 90°, 扫过来时的方向
    (0, 0, 0.5),
    (0, 100, spin(45)),     # 回正 (45-90+45 = 0°)
    (0, 0, 0.5),
    (PROBE, 0, 2.4),        # 换地点: 走一段 (全表最长探步)
    (0, 0, 0.5),
    (0, -100, spin(50)),    # 新地点扫一轮
    (0, 0, 0.5),
    (PROBE, 0, 1.8),        # 继续挪
    (0, 0, 0.5),
    (CREEP, 0, 1.2),        # 最后凑近一点
    (0, 0, 1.0),            # 停下打量
    (0, 100, spin(50)),     # 末瞥左 50° — 归零轮: 抵消第二轮扫视 -50° (净朝向 0)
    (0, 0, 0.5),
    STOP_FRAME,
]

HESITANT = [
    # 原型: 面对EVE时的趋避试探 — approach-avoidance, 压缩到 2 轮进退(4 拍)
    (SLOW, 0, 0.6),
    (0, 0, 0.35),
    (-BACK, 0, 0.6),
    (0, 0, 0.4),
    (SLOW, 0, 0.5),
    (0, 0, 0.35),
    (-BACK, 0, 0.8),
    (0, 0, 0.7),
    (0, 100, spin(35)),
    (0, 0, PAUSE_GAP),
    (0, -100, spin(65)),
    (0, 0, PAUSE_GAP),
    (0, 100, spin(30)),
    (0, 0, 0.8),
    (CREEP, 0, 1.0),
    (0, 0, 0.3),
    (CRUISE, 0, 1.0),
    STOP_FRAME,
]

STARTLED = [
    # 原型: freeze → recoil → investigate surroundings → leave
    # 不做成 freeze → panic → flee, 结尾不再是长时间冲刺
    # 2026-08-24 pilot: 0.15s 间隙短于固件 0.32s 减速尾, 张望糊成一团 →
    # 间隙抬到 ≥0.35s 每个动作看得清; 转身后前进离场
    # 2026-08-27 归零轮: 逃开后再猛回头 180° 面对威胁定格(动物行为原型),
    # 净朝向回 0; 代价: 新增 5.0s Y100 满幅自转段 = 实验池最长连续饱和自转
    # (calibrate 5.0s 自转段同规格先例), 循环播放照旧列入 L298 测温观察
    (0, 0, 0.3),            # freeze
    (-SPRINT, 0, 0.4),      # recoil 后缩
    (0, 0, 0.4),
    (0, 100, spin(45)),     # 张望左 45°
    (0, 0, 0.35),
    (0, -100, spin(90)),    # 张望右 90° (甩过头 = double-take)
    (0, 0, 0.35),
    (0, -100, spin(135)),   # 转身背向威胁 (此刻朝向 = 180°)
    (0, 0, 0.35),
    (SPRINT, 0, 0.9),       # 前进冲刺离场
    (0, 0, SPRINT_GAP),
    (0, 100, spin(180)),    # 猛回头面对威胁定格 — 归零轮: 逃开+回头确认, 净朝向回 0
    (0, 0, 0.5),
    STOP_FRAME,
]

EAGER = [
    # 原型: 兴冲冲地朝目标靠近 + 目标附近的playful movement
    # 明确不框定为"递送/展示物体"(履带无手臂, affordance不匹配),
    # 定义收敛为: eager approach / socially directed excitement
    # 2026-08-24 pilot: pivot(180) 读作"掉头" → 改 pivot(90) "绕一下继续跑";
    # 弧线前间隙 0.2→0.35s (短于斜坡的间隙让残余角速度带进弧线);
    # 结尾冲刺 0.6→1.0s (0.6s 有效位移太小, 读作"走了一点点")
    # 2026-08-27 归零轮: 弧线前扫视 35°→90° (净朝向 +55°→0°), 贴身弧线节拍不动;
    # 注: 开头碎步 (0.18s通/0.12s停) 时序上与被禁的 w/q 满速脉冲同形, 但不违例 —
    # X/Y 模拟量指令、仅3周期有界、固件斜坡下峰值约56%满速, pilot实机跑过无欠压
    (SPRINT, 0, 0.18),
    (0, 0, 0.12),
    (SPRINT, 0, 0.18),
    (0, 0, 0.12),
    (SPRINT, 0, 0.18),
    (0, 0, 0.35),           # 碎步收尾停稳, 残余平移不糊进张望
    (0, -100, spin(90)),    # 看到右边的目标 (90° 扫视) — 归零轮: 35→90° 抵消贴身弧线 +90°
    (0, 0, 0.35),
    (50, 86, pivot(90)),
    (0, 0, 0.35),
    (SPRINT, 0, 1.0),
    (0, 0, SPRINT_GAP),
    STOP_FRAME,
]

SOLITARY = [
    # 原型: WALL-E日复一日独自作业/独自回住处的生活状态,
    # 是character disposition而非单一情绪, 命名避免用LONELY这种
    # 难以用运动学直接证成的心理状态词
    # 关键设计: 低能量 + 长停顿 + 无明确social target,
    # 刻意不做左右张望 — 张望会让它读成CURIOUS
    # (2026-08-27: 用户指定走完后加左转 120° 换向拍, 覆盖此前"零转向"设计)
    # 2026-08-24 pilot: X30可动、观感好, 但 1.8/1.2/0.8s 位移不可见 →
    # 移动相拉长至 ~8s (位移翻倍), 停顿适度压缩, 总长 14s;
    # 档位随后 30→35 再增 17% 位移
    (0, 0, 1.5),
    (DRIFT, 0, 3.5),        # 独自前行 — 每段走够一段路程
    (0, 0, 1.2),
    (DRIFT, 0, 2.8),
    (0, 0, 1.0),
    (DRIFT, 0, 2.0),
    (0, 0, 1.5),
    (0, 100, spin(120)),    # 走完左转 120° — 2026-08-27 用户指定: 连播时的换向连接拍
                            # (打破本表"零转向"设计, 用户裁决优先; 净朝向 +120°)
    (0, 0, 0.5),
    STOP_FRAME,
]

# ==================== pilot 候选层 (2026-08-24 新增, 与锁定六行为并存评估) ====================
# 设计思路: 履带表达"意图/行为故事"而非抽象情绪; 角度全部 ≥25° (CALIBRATE
# 验证下限), 换向间隙全部 ≥0.35s (≥固件 0.32s 减速尾, 动作个个看得清)。
# 定稿剪枝前与锁定行为同池, 最终实验集合从这里挑。


SHY = [
    # 候选·害羞靠近 — 与HESITANT的区别: HESITANT是正面对峙的进退+张望后下定决心;
    # SHY是空间策略 — 侧身弧线绕行接近, 回避性偏头(只偏一次头, 不张望),
    # 全程不正视目标, 结尾转回来停在目标旁边
    # 2026-08-25 重构: 从进退节律改为弧线, 与HESITANT运动学分离;
    # 朝向链 -35°+45°+30° = +40°, 末帧右转 35° → +5°(几乎面对目标);
    # 偏头 35° (评审: 25° 偏小读不出"回避");
    # 2026-08-27 归零轮: 本表零改动 — 净朝向 +5° 由 discover 末瞥 -75° 对冲
    (SLOW, 0, 0.8),         # 慢慢靠近(直行)
    (0, 0, 0.5),
    (0, -100, spin(35)),    # 偏头避开视线 (右偏 35°)
    (0, 0, 0.5),
    (50, 86, pivot(45)),    # 侧身弧线绕行
    (0, 0, 0.5),
    (CREEP, 0, 1.2),        # 侧身蠕行
    (0, 0, 0.5),
    (-BACK, 0, 0.5),        # 怯一下, 退一点
    (0, 0, 0.5),
    (50, 86, pivot(30)),    # 继续弧线兜过去 (贴身 30° = 已验证最小实用角, 归零轮保持不动)
    (0, 0, 0.5),
    (CREEP, 0, 1.6),        # 最后很慢很慢地靠近
    (0, 0, 0.6),
    (0, -100, spin(35)),    # 转回来, 停在目标旁边
    (0, 0, 0.8),
    STOP_FRAME,
]

WIGGLE = [
    # 候选·开心扭扭 — 履带版"摇尾巴"; 2026-08-25 放大角度词汇:
    # 270/90° 大旋转 + 全速冲刺节奏, 净朝向归零 (270+90-270-90 = 0°)
    # 热学: 右向 360° 拆为 270+90 两段(中间静止散热窗), 最长连续饱和段
    # = 7.5s(spin270); 本表饱和累计仍全池第一, 循环播放留意 L298 温度
    # (SPRINT, 0, 1.2),       # 快速向前一点
    # (0, 0, 0.5),
    # (-SPRINT, 0, 0.7),      # 快速退一点
    # (0, 0, 0.5),
    # (0, 100, spin(270)),    # 左 270°
    # (0, 0, 0.5),
    # (SPRINT, 0, 0.3),       # 快速向前一点
    # (0, 0, 0.5),
    # (0, 100, spin(90)),     # 左 90°
    # (0, 0, 0.5),
    # (SPRINT, 0, 1.2),       # 快速向前一点
    # (0, 0, 0.5),
    # (-SPRINT, 0, 0.7),      # 快速退一点
    # (0, 0, 0.7),
    # (0, -100, spin(270)),   # 右 270° (回正前段)
    # (0, 0, 0.5),
    # (0, -100, spin(90)),    # 右 90° (回正)
    # (0, 0, 0.5),
    # (SPRINT, 0, 0.3),       # 再向前一点
    # (0, 0, SPRINT_GAP),
    # STOP_FRAME,
    (SPRINT, 0, 0.45),      # 兴冲冲进场一小冲
    (0, 0, 0.35),
    (15, 85, 1.45),         # ─ 蛇形段: 左甩 (右轨满幅) ─
    (15, -85, 1.45),        #   右甩 (左轨满幅, 镜像)
    (15, 85, 1.45),
    (15, -85, 1.45),
    (0, 0, 0.50),           # 乐句换气 + 散热窗
    (15, 85, 1.45),
    (15, -85, 1.45),        # 末拍回正, 净朝向归零
    (SPRINT, 0, 0.40),      # 收尾窜一小步定格
    (0, 0, SPRINT_GAP),
    STOP_FRAME,
]

DISCOVER = [
    # 候选·发现东西 — 与HESITANT的区别: 退是换视角打量物件, 不是社交趋避;
    # 与CURIOUS的分工: 本行为旋转支配 — 单物件环绕端详, 结尾退走
    # 2026-08-25 修补: 旋转↔直行切换处补 ≥0.5s 间隙(原两处无间隙,
    # 残余角速度会糊进直行); 两段连续静止合并为单个长"研究"帧
    # 2026-08-27 归零轮: 结尾回望原为 -75°(对冲+70°); 用户裁决改为左转 +75°,
    # 本表净朝向 +145°, 归零链解除, 连播模式见 docs 运动范围保证节
    (CRUISE, 0, 1.5),       # 向前 (例行作业中)
    (0, 0, 1.0),            # 突然停 — 发现了什么
    (-BACK, 0, 2.0),        # 退一点, 换个视角
    (0, 0, 0.5),
    (0, -100, spin(60)),    # 右转 60° 端详
    (0, 0, 0.5),
    (CRUISE, 0, 1.5),       # 换个角度再看
    (0, 0, 0.5),
    (0, 100, spin(130)),    # 左转 130° 绕到另一侧端详
    (0, 0, 0.5),
    (DRIFT, 0, 1.0),        # 重新靠近
    (0, 0, 0.7),
    (DRIFT, 0, 1.5),        # 非常慢地凑近端详
    (0, 0, 2.3),            # 停住研究 (长静止)
    (-BACK, 0, 1.0),        # 看完默默退开
    (0, 100, spin(75)),     # 退开时左转 75° — 2026-08-27 用户指定由右转改左转
                            # (净朝向 +70°+75° = +145°, 不再对冲 shy 的 +5°)
    (0, 0, 0.5),
    STOP_FRAME,
]


# ==================== 注册表 ====================

ANIMATIONS = {
    # --- 实验池 8 行为 (2026-08-27 归零轮 + 用户实测连播编排) ---
    # 字典顺序 = UI 卡片顺序 = 演示连播顺序(用户实测串联流畅):
    # solitary 换向拍(+120°)与 discover 左转(+75°)使单轮终朝向 ≈ -90°,
    # 连续 4 轮近似回环回起点; 单播仍各自成立, 详见 docs 运动范围保证节
    'curious':   CURIOUS,
    'solitary':  SOLITARY,
    'hesitant':  HESITANT,
    'wiggle':    WIGGLE,
    'shy':       SHY,
    'discover':  DISCOVER,
    'eager':     EAGER,
    'startled':  STARTLED,
    }

DISPLAY = {
    'curious':   'curious',
    'solitary':  'solitary',
    'hesitant':  'hesitant',
    'wiggle':    'wiggle',
    'shy':       'shy',
    'discover':  'discover',
    'eager':     'eager',
    'startled':  'startled',
}

# 实验候选池: 锁定五行为 + pilot 候选; 定稿剪枝前整池可用,
# calibrate 等工程序列已排除
EXPERIMENT_ANIMATIONS = {k: v for k, v in ANIMATIONS.items() if k != 'calibrate'}


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
            if thread.is_alive():
                logging.warning('Track animation thread did not exit within 2s')
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
                    # 从表格中段复活只会呈现残缺节拍, 不如体面中止。
                    # 失败帧机器人根本没收到, 不睡满帧长 — 短探活间隔快速累计
                    fail_streak += 1
                    if fail_streak >= 3:
                        logging.warning(f'Track animation [{name}] aborted: serial link lost')
                        return
                    self._sleep(stop_event, 0.2)
                    continue
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
