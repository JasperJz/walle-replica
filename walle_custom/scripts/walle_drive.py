# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
WALL-E 履带行为集脚本 (增强版)
================================

通过串口给 Arduino 发小写单字符指令,远程驱动履带电机。

【指令协议】(对应 wall-e.ino evaluateSerial, 严格一致):
    w  前进    moveValue = +pwmspeed (满速 255)
    s  后退    moveValue = -pwmspeed
    a  左转    turnValue = -pwmspeed (原地左转)
    d  右转    turnValue = +pwmspeed (原地右转)
    q  停止    moveValue = 0, turnValue = 0

    注意: 固件里 w/s/a/d 是满速 255, 没有调速命令。
    本脚本用 pulse() 脉冲驱动 (走-停-走-停) 来等效降速,
    这样能做出慢速、犹豫、试探等不同性格的动作。

【用法】
    python3 ~/walle_drive.py list          列出所有行为
    python3 ~/walle_drive.py showcase      表演集锦(全部串一遍)
    python3 ~/walle_drive.py celebrate     庆祝舞
    python3 ~/walle_drive.py wander 3      随机漫游 3 轮
    python3 ~/walle_drive.py spin 2        原地转 2 圈
    python3 ~/walle_drive.py forward 2     前进 2 秒
    python3 ~/walle_drive.py stop          紧急停止

【关键时序】
    Arduino 复位后, setup() 里的 softStart() 阻塞约 3.5 秒。
    connect() 会循环读串口, 直到 "entering main loop" 才发指令。
    这是稳定驱动电机的核心。

【如何加自己的行为】
    1. 照文件末尾的写法, 用 cmd() + pulse() + time.sleep() 组合动作
    2. 在 ACTIONS 字典里注册一行: '名字': (函数名, '说明')
    3. 完成! python3 ~/walle_drive.py 名字 即可调用
"""
import serial, sys, time, subprocess, random

# ==================== 可调参数 ====================
PORT = '/dev/ttyACM0'        # Arduino 串口设备
BAUD = 115200                # 波特率, 必须和固件 Serial.begin 一致
BOOT_TIMEOUT = 12            # 等固件启动的最长秒数 (softStart 约 3.5s)
DEFAULT_DUR = 1.0            # forward/backward/left/right 默认秒数
TURN_90 = 0.9                # 约 90 度转弯秒数 —— 实地微调

# --- 传动补偿参数 (重点调这里) ---
# 这台机器人前进传动效率高、后退低, 组合动作也有整体损耗。
# 以下三个常量集中控制全局补偿, 改一处全局生效。
BACK_BIAS  = 1.4             # 后退时长乘数: 传动效率低就放大后退时间。
                             #   1.0=不补偿; 1.4=后退慢40%就给40%额外时间。
                             #   实测: 后退明显走不远就调大(1.5/1.6)。
GAP        = 0.25            # 动作之间的标准过渡间隙(秒)。
                             #   电机刚停有惯性, 间隙太小下个动作会被吃掉。
                             #   组合动作衔接不顺就调大(0.3/0.35)。
TURN_GAP   = 0.3             # 转弯后额外停顿(秒), 比直线间隙更大,
                             #   因为转弯惯性强, 需要更多时间稳住。

_stopped_service = False     # 本脚本是否暂停过 web 服务


# ==================== 串口与 web 服务管理 ====================

def acquire_port():
    """打开串口; 若被 web 服务占用则自动暂停它。"""
    global _stopped_service
    try:
        return serial.Serial(PORT, BAUD, timeout=0.1)
    except serial.SerialException:
        r = subprocess.run(
            ['sudo', '-n', 'systemctl', 'stop', 'walle.service'],
            capture_output=True)
        if r.returncode != 0:
            print('串口被占, 无法自动停止 walle.service。')
            print('请手动运行: sudo systemctl stop walle.service')
            sys.exit(1)
        _stopped_service = True
        print('(已暂停 walle.service, 结束后自动恢复)')
        time.sleep(1.5)
        return serial.Serial(PORT, BAUD, timeout=0.1)


def restore():
    """如果本脚本暂停过 web 服务, 结束后恢复它。"""
    if _stopped_service:
        subprocess.run(['sudo', '-n', 'systemctl', 'start',
                        'walle.service'], capture_output=True)


def connect():
    """打开串口, 等固件 boot 完成后再返回。"""
    s = acquire_port()
    print('等待固件启动...')
    buf = ''
    deadline = time.time() + BOOT_TIMEOUT
    while time.time() < deadline:
        if s.in_waiting:
            chunk = s.read_all().decode(errors='replace')
            buf += chunk
            sys.stdout.write(chunk)
            sys.stdout.flush()
            if 'entering main loop' in buf or 'main loop' in buf:
                print('固件就绪。')
                return s
        time.sleep(0.1)
    print('警告: 未读到启动横幅, 继续尝试')
    return s


def cmd(s, ch):
    """发送单个小写指令字符 + 换行。固件遇 \\n 才处理。"""
    s.write((ch + '\n').encode())
    s.flush()


def burst(s, ch, secs):
    """发送指令并保持 secs 秒 (期间持续通电)。"""
    cmd(s, ch)
    time.sleep(secs)


def pulse(s, ch, duration, on=0.18, off=0.12):
    """脉冲驱动: 间歇通电, 等效降速。

    固件的 w/s/a/d 是满速 255, 没法直接调速。
    这里用 "通电 on 秒 -> 停 off 秒" 循环来模拟慢速。
    on 越小 / off 越大 = 越慢。
    典型值:  慢速 on=0.18 off=0.12
             超慢 on=0.10 off=0.20 (一蹭一蹭)
             极快 on=0.30 off=0.05 (几乎全速但带顿挫感)
    """
    end = time.time() + duration
    while time.time() < end:
        cmd(s, ch)
        time.sleep(on)
        cmd(s, 'q')
        time.sleep(off)


# --- 传动补偿封装 (后退专用) ---
# 因为后退传动效率低, 所有后退调用统一走这两个函数,
# 自动乘 BACK_BIAS 放大时长。调 BACK_BIAS 就全局生效。
# 前进/转弯不受影响, 直接用 burst()/pulse()。

def back(s, secs):
    """后退(持续通电), 自动应用 BACK_BIAS 补偿。"""
    burst(s, 's', secs * BACK_BIAS)

def pback(s, duration, on=0.18, off=0.12):
    """脉冲后退(等效慢速), 自动应用 BACK_BIAS 补偿。"""
    pulse(s, 's', duration * BACK_BIAS, on, off)

def gap(s=None, g=GAP):
    """标准过渡间隙。s 参数不用传, 纯为调用风格统一。"""
    time.sleep(g)


# ==================== 基础动作 ====================

def a_stop(s, **k):
    cmd(s, 'q')

def a_forward(s, d=DEFAULT_DUR, **k):
    cmd(s, 'w'); time.sleep(d); cmd(s, 'q')

def a_backward(s, d=DEFAULT_DUR, **k):
    back(s, d)  # 自动补偿后退传动效率

def a_left(s, d=0.8, **k):
    cmd(s, 'a'); time.sleep(d); cmd(s, 'q')

def a_right(s, d=0.8, **k):
    cmd(s, 'd'); time.sleep(d); cmd(s, 'q')


# ==================== 固定行为集 ====================

def b_test(s, **k):
    """链路自检: 前进 2 秒。"""
    print('测试: 前进 2 秒...')
    burst(s, 'w', 2.0); cmd(s, 'q')


def b_wiggle(s, **k):
    """左右摇摆 4 次, 逐渐加快。"""
    for i, t in enumerate([0.5, 0.4, 0.3, 0.2]):
        burst(s, 'a', t); cmd(s, 'q'); time.sleep(0.15)
        burst(s, 'd', t * 2); cmd(s, 'q'); time.sleep(0.15)
        burst(s, 'a', t); cmd(s, 'q'); time.sleep(0.15)


def b_scan(s, **k):
    """原地慢速扫描: 左转 -> 停 -> 右转 -> 停 -> 回中。用脉冲慢转。"""
    pulse(s, 'a', 2.5); cmd(s, 'q'); time.sleep(TURN_GAP)
    pulse(s, 'd', 5.0); cmd(s, 'q'); time.sleep(TURN_GAP)
    pulse(s, 'a', 2.5); cmd(s, 'q')


def b_patrol(s, loops=1, **k):
    """方形巡逻: 前进 + 右转 90, 重复 loops*4 次。"""
    for _ in range(loops * 4):
        burst(s, 'w', 1.5); cmd(s, 'q'); time.sleep(GAP)
        burst(s, 'd', TURN_90); cmd(s, 'q'); time.sleep(TURN_GAP)


def b_happy(s, **k):
    """兴奋: 冲刺 + 加速摇摆 + 原地旋转。"""
    burst(s, 'w', 0.5); cmd(s, 'q'); time.sleep(GAP)
    for t in [0.2, 0.15, 0.1]:
        burst(s, 'a', t); cmd(s, 'q')
        burst(s, 'd', t * 2); cmd(s, 'q')
        burst(s, 'a', t); cmd(s, 'q')
    time.sleep(0.1)
    burst(s, 'd', 1.2); cmd(s, 'q')   # 旋转一周


def b_backup(s, **k):
    """后退 + 转身离开。"""
    back(s, 0.8); cmd(s, 'q'); time.sleep(GAP)
    burst(s, 'd', TURN_90 * 2); cmd(s, 'q'); time.sleep(TURN_GAP)
    burst(s, 'w', 0.8); cmd(s, 'q')   # 前进追回后退的距离


def b_dance(s, **k):
    """舞步: 前后左右交替, 重复 3 轮, 每轮节奏不同。"""
    tempos = [0.5, 0.35, 0.25]
    for t in tempos:
        burst(s, 'w', t); cmd(s, 'q'); time.sleep(0.12)
        burst(s, 'd', t); cmd(s, 'q'); time.sleep(0.12)
        back(s, t); cmd(s, 'q'); time.sleep(0.12)        # 后退自动补偿
        burst(s, 'a', t); cmd(s, 'q'); time.sleep(0.12)
        time.sleep(0.1)


def b_figure8(s, **k):
    """画 8 字: 右转一圈 + 左转一圈, 中间穿插前进。"""
    burst(s, 'w', 0.6); cmd(s, 'q'); time.sleep(GAP)
    burst(s, 'd', 2.4); cmd(s, 'q'); time.sleep(TURN_GAP)   # 右转一圈
    burst(s, 'w', 0.8); cmd(s, 'q'); time.sleep(GAP)
    burst(s, 'a', 2.4); cmd(s, 'q'); time.sleep(TURN_GAP)   # 左转一圈
    burst(s, 'w', 0.6); cmd(s, 'q')


def b_zigzag(s, **k):
    """蛇形前进: 左转前进右转前进, 重复 4 段。"""
    for _ in range(4):
        burst(s, 'a', 0.4); cmd(s, 'q'); time.sleep(0.15)
        burst(s, 'w', 0.8); cmd(s, 'q'); time.sleep(0.15)
        burst(s, 'd', 0.8); cmd(s, 'q'); time.sleep(0.15)
        burst(s, 'w', 0.8); cmd(s, 'q'); time.sleep(0.15)


def b_celebrate(s, **k):
    """庆祝: 快速冲刺 -> 原地旋转两周 -> 摇摆 -> 胜利前进。"""
    burst(s, 'w', 0.4); cmd(s, 'q'); time.sleep(GAP)
    burst(s, 'd', 1.0); cmd(s, 'q'); time.sleep(TURN_GAP)
    burst(s, 'a', 2.0); cmd(s, 'q'); time.sleep(TURN_GAP)     # 反转两周
    b_wiggle(s)
    time.sleep(GAP)
    burst(s, 'w', 1.0); cmd(s, 'q'); time.sleep(GAP)
    burst(s, 'd', 0.8); cmd(s, 'q'); time.sleep(TURN_GAP)
    burst(s, 'w', 0.8); cmd(s, 'q')


def b_wander(s, rounds=3, **k):
    """随机漫游: 每轮随机选方向和时长, 像在探索。"""
    moves = ['w', 's', 'a', 'd']
    for _ in range(int(rounds)):
        m = random.choice(moves)
        dur = random.uniform(0.4, 1.5)
        # 后退自动补偿, 前进/转弯原速
        if m == 's':
            back(s, dur); cmd(s, 'q')
        else:
            burst(s, m, dur); cmd(s, 'q')
        time.sleep(random.uniform(0.2, 0.45))


def b_spin(s, count=2, **k):
    """原地旋转: 先右转 count 圈, 再左转 count 圈。"""
    count = int(count)
    for _ in range(count):
        burst(s, 'd', 1.2); cmd(s, 'q'); time.sleep(TURN_GAP)
    for _ in range(count):
        burst(s, 'a', 1.2); cmd(s, 'q'); time.sleep(TURN_GAP)


def b_waltz(s, **k):
    """华尔兹三拍: 前进-侧转-侧转, 优雅慢速, 重复 4 小节。"""
    for _ in range(4):
        pulse(s, 'w', 0.7, on=0.25, off=0.08); cmd(s, 'q'); time.sleep(GAP)
        pulse(s, 'd', 0.7, on=0.25, off=0.08); cmd(s, 'q'); time.sleep(TURN_GAP)
        pulse(s, 'a', 0.7, on=0.25, off=0.08); cmd(s, 'q'); time.sleep(TURN_GAP)


def b_explore(s, **k):
    """探索模式: 前进一段 -> 停下张望(左右转) -> 换方向继续。"""
    for _ in range(3):
        pulse(s, 'w', 1.5, on=0.2, off=0.1); cmd(s, 'q'); time.sleep(GAP)
        burst(s, 'a', 0.6); cmd(s, 'q'); time.sleep(TURN_GAP)
        burst(s, 'd', 1.2); cmd(s, 'q'); time.sleep(TURN_GAP)
        burst(s, 'a', 0.6); cmd(s, 'q'); time.sleep(GAP)
        turn = random.choice(['a', 'd'])
        burst(s, turn, random.uniform(0.3, 0.8)); cmd(s, 'q'); time.sleep(TURN_GAP)


def b_panic(s, **k):
    """慌张: 快速短促不规则运动, 像受惊乱跑。"""
    for _ in range(8):
        m = random.choice(['w', 's', 'a', 'd'])
        dur = random.uniform(0.1, 0.35)
        if m == 's':
            back(s, dur); cmd(s, 'q')
        else:
            burst(s, m, dur); cmd(s, 'q')
        time.sleep(random.uniform(0.05, 0.12))


def b_tease(s, **k):
    """调皮试探: 前进一点 -> 退回 -> 再前进多一点 -> 退回。"""
    for d in [0.3, 0.5, 0.7]:
        pulse(s, 'w', d, on=0.15, off=0.1); cmd(s, 'q'); time.sleep(GAP)
        back(s, d * 0.8); cmd(s, 'q'); time.sleep(0.35)     # 后退自动补偿
    burst(s, 'w', 1.0); cmd(s, 'q')                        # 最后冲出去
    burst(s, 'w', 0.8); cmd(s, 'q')


def b_orbit(s, **k):
    """圆弧轨迹: 前进的同时持续微转, 画一个大圆。"""
    for _ in range(8):
        burst(s, 'w', 0.5); cmd(s, 'q'); time.sleep(0.08)
        burst(s, 'd', 0.3); cmd(s, 'q'); time.sleep(0.08)


def b_slinky(s, **k):
    """弹簧步: 超慢一蹭一蹭前进, 像弹簧玩具。"""
    pulse(s, 'w', 4.0, on=0.12, off=0.18); cmd(s, 'q')


def b_bow(s, **k):
    """鞠躬: 后退一步 -> 停顿 -> 前进致敬。"""
    pback(s, 0.8, on=0.15, off=0.08); cmd(s, 'q'); time.sleep(0.5)  # 脉冲后退自动补偿
    burst(s, 'w', 0.7); cmd(s, 'q'); time.sleep(GAP)
    b_wiggle(s)


def b_moonwalk(s, **k):
    """太空步: 后退为主, 中间穿插小幅转向。"""
    for _ in range(3):
        pback(s, 0.7, on=0.2, off=0.1); cmd(s, 'q'); time.sleep(0.1)
        burst(s, 'a', 0.2); cmd(s, 'q'); time.sleep(0.1)
        pback(s, 0.7, on=0.2, off=0.1); cmd(s, 'q'); time.sleep(0.1)
        burst(s, 'd', 0.2); cmd(s, 'q'); time.sleep(0.1)


def b_showcase(s, **k):
    """表演集锦: 把所有表演类行为串成一场完整演出。"""
    lineup = [
        ('鞠躬开场', b_bow),
        ('华尔兹', b_waltz),
        ('蛇形前进', b_zigzag),
        ('庆祝舞', b_celebrate),
        ('调皮试探', b_tease),
        ('螺旋扫描', b_scan),
        ('弹簧步', b_slinky),
        ('慌张乱跑', b_panic),
        ('太空步', b_moonwalk),
        ('谢幕摇摆', b_wiggle),
    ]
    for name, fn in lineup:
        print(f'  ~ {name} ~')
        fn(s)
        time.sleep(0.6)


# ==================== 行为注册表 ====================
ACTIONS = {
    # 基础
    'stop':     (a_stop,      '立即停止'),
    'forward':  (a_forward,   '前进 [秒]'),
    'backward': (a_backward,  '后退 [秒]'),
    'left':     (a_left,      '原地左转 [秒]'),
    'right':    (a_right,     '原地右转 [秒]'),
    # 测试 & 巡逻
    'test':     (b_test,      '前进 2 秒测试'),
    'scan':     (b_scan,      '慢速扫描(脉冲)'),
    'patrol':   (b_patrol,    '方形巡逻 [圈]'),
    'orbit':    (b_orbit,     '圆弧轨迹'),
    'zigzag':   (b_zigzag,    '蛇形前进'),
    'figure8':  (b_figure8,   '画 8 字'),
    # 表演类
    'wiggle':   (b_wiggle,    '加速摇摆'),
    'happy':    (b_happy,     '冲刺 + 旋转'),
    'dance':    (b_dance,     '三拍舞步'),
    'waltz':    (b_waltz,     '华尔兹(慢速)'),
    'celebrate':(b_celebrate, '庆祝舞(全套)'),
    'spin':     (b_spin,      '原地旋转 [圈]'),
    # 性格类
    'explore':  (b_explore,   '探索模式'),
    'wander':   (b_wander,    '随机漫游 [轮]'),
    'tease':    (b_tease,     '调皮试探'),
    'panic':    (b_panic,     '慌张乱跑'),
    'slinky':   (b_slinky,    '弹簧步(超慢)'),
    'bow':      (b_bow,       '鞠躬'),
    'moonwalk': (b_moonwalk,  '太空步'),
    'backup':   (b_backup,    '后退转身'),
    # 集锦
    'showcase': (b_showcase,  '表演集锦(全串)'),
}


# ==================== 主入口 ====================

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ACTIONS:
        if len(sys.argv) >= 2 and sys.argv[1] == 'list':
            print('可用行为:')
            for k, (_, d) in ACTIONS.items():
                print(f'  {k:12s} {d}')
            return
        print(__doc__)
        return

    name = sys.argv[1]
    func, _ = ACTIONS[name]
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    kw = {}
    if name in ('patrol',) and arg:
        kw['loops'] = int(arg)
    elif name in ('forward', 'backward', 'left', 'right') and arg:
        kw['d'] = float(arg)
    elif name == 'wander' and arg:
        kw['rounds'] = int(arg)
    elif name == 'spin' and arg:
        kw['count'] = int(arg)

    print(f'执行: {name}')
    try:
        s = connect()
        try:
            func(s, **kw)
        finally:
            cmd(s, 'q')
            s.close()
        print('完成')
    except KeyboardInterrupt:
        print('\n已中断, 已发停止指令')
    finally:
        restore()


if __name__ == '__main__':
    main()
