# WALL-E 远程操作手册

覆盖:烧录固件(校准 / 主程序)、SSH 远程测试履带、远程校准舵机、修改行为脚本、常见问题。

所有命令从 Mac 执行,树莓派不接显示器。

---

## 整体架构

```
┌──────────┐   SSH (WiFi)   ┌──────────────────┐   USB串口    ┌──────────┐
│  你的Mac  │ ─────────────▶ │   树莓派 (Pi)     │ ──────────▶ │  Arduino │
│  终端窗口  │                │  walle_drive.py   │ /dev/ttyACM0│  Uno 固件 │
└──────────┘                 │  walle_calibrate  │ 115200 baud └──────────┘
                             └──────────────────┘                  │
                                                                   ▼
                                                          PCA9685 舵机扩展板
                                                          + Motor Shield 履带
```

三台机器各司其职:

- **Arduino** — 跑 `.ino` 固件,真正控制硬件。同一时刻只能跑一个程序。
- **树莓派** — 跑 Python 脚本,通过串口给 Arduino 发指令。本手册的工具都在这里。
- **Mac** — 只负责 SSH 进树莓派敲命令。

`.ino` 是烧在 Arduino 芯片里的固件,Python 不能替代它。Python 的角色是「串口遥控器」。

---

## 一、烧录固件

### 每次烧录前必做

烧录会独占串口,而 web 服务 `walle.service` 平时占着它。固定套路:

```bash
ssh walle-2.local                        # 1. 登录树莓派
sudo systemctl stop walle.service        # 2. 停掉 web 服务,释放串口
pkill -f walle_drive.py                  # 3. 确认没有别的脚本占着串口(没有就忽略报错)
```

### 1.1 烧录校准程序

用于校准舵机机械极限位置。

```bash
~/bin/arduino-cli compile --fqbn arduino:avr:uno \
  --library ~/Arduino/libraries/Adafruit_PWM_Servo_Driver_Library \
  ~/walle-replica/wall-e_calibration

~/bin/arduino-cli upload -p /dev/ttyACM0 --fqbn arduino:avr:uno \
  ~/walle-replica/wall-e_calibration
```

看到 `New upload port: /dev/ttyACM0 (serial)` 就是成功。

### 1.2 烧录主程序

用于日常驾驶履带(也包含舵机动画)。

```bash
~/bin/arduino-cli compile --fqbn arduino:avr:uno \
  --library ~/Arduino/libraries/Adafruit_PWM_Servo_Driver_Library \
  ~/walle-replica/wall-e

~/bin/arduino-cli upload -p /dev/ttyACM0 --fqbn arduino:avr:uno \
  ~/walle-replica/wall-e
```

### 烧录后

```bash
sudo systemctl start walle.service       # 恢复 web 服务
exit                                      # 回到 Mac
```

### 烧录失败排查

| 报错 | 原因 | 解决 |
|------|------|------|
| `programmer is not responding` | 串口被占用 | 停 `walle.service`,确认无 python 脚本在跑 |
| `Compiled sketch not found` | 跳过了 compile | 先 compile 再 upload |
| 连不上 `walle-2.local` | 树莓派掉线 | 看板子红灯,等它重启回来 |

---

## 二、SSH 远程测试履带

前提:芯片里烧的是**主程序**(不是校准程序)。

### 2.1 登录并运行

```bash
ssh walle-2.local
python3 ~/walle_drive.py test
```

`test` 会等固件启动完成(约 3.5 秒)、前进 2 秒、停止。履带动了 = 链路正常。

### 2.2 内置行为一览

```bash
python3 ~/walle_drive.py list          # 列出所有行为
python3 ~/walle_drive.py forward 2     # 前进 2 秒
python3 ~/walle_drive.py backward 1    # 后退 1 秒
python3 ~/walle_drive.py left 0.8      # 左转
python3 ~/walle_drive.py right 0.8     # 右转
python3 ~/walle_drive.py wiggle        # 左右摇摆
python3 ~/walle_drive.py scan          # 原地慢速扫描
python3 ~/walle_drive.py patrol 2      # 方形巡逻 2 圈
python3 ~/walle_drive.py happy         # 冲刺 + 摇摆
python3 ~/walle_drive.py backup        # 后退转身
python3 ~/walle_drive.py dance         # 前后转向交替
python3 ~/walle_drive.py stop          # 紧急停止
```

带 `[秒]` / `[圈]` 的行为可传数字参数,不传用默认值。

### 2.3 自己编排动画

编辑 `~/walle_drive.py`(在树莓派上 `nano ~/walle_drive.py`)。核心就是:发 `w` 保持一会、发 `q` 停一会、循环。详见脚本里的注释和本手册第五章。

### 2.4 指令协议(对照 wall-e.ino 第 320-342 行)

脚本只发小写单字符:

| 发 | 固件动作 | 说明 |
|----|----------|------|
| `w` | 前进 | moveValue = pwmspeed(全速 255) |
| `s` | 后退 | moveValue = -pwmspeed |
| `a` | 左转 | turnValue = -pwmspeed |
| `d` | 右转 | turnValue = pwmspeed |
| `q` | 停止 | 全部归零 |

注意:固件里小写 `w/s/a/d` 会同时让头部舵机转向,这是固件设计。大写 `Y/X` 只驱动电机但实测不稳定,所以不用。

---

## 三、远程校准舵机(可选,现在不做也没关系)

### 3.1 烧校准固件

按第 1.1 烧校准程序。

### 3.2 运行校准工具

```bash
python3 ~/walle_calibrate.py
```

特点:

- 自动识别固件:检测到主程序会**拒绝运行**(防止 `a`/`d` 误驱动电机蹿出去)
- 单键操作,不用按回车
- 校准值自动存到 `~/calibration_result.txt`

按键:`d`/`a` 大步 ±10,`c`/`z` 微调 ±1,`n` 确认进入下一个。

### 3.3 应用校准值

把 `~/calibration_result.txt` 的 `preset[][2]` 粘进 `~/walle-replica/wall-e/wall-e.ino` 第 144-150 行,然后按 1.2 烧回主程序。

---

## 四、编排属于自己的行为集

脚本里每一个「行为」就是一个 Python 函数。模板:

```python
def b_myaction(s, **k):
    # 每个动作: 发指令字符 -> sleep 保持 -> 发 q 停止
    cmd(s, 'w'); time.sleep(0.5)   # 前进 0.5 秒
    cmd(s, 'q'); time.sleep(0.2)   # 停 0.2 秒
    cmd(s, 'd'); time.sleep(0.9)   # 右转约 90 度
    cmd(s, 'q')                    # 收尾停止
```

然后在 `ACTIONS` 字典里注册一行:

```python
    'myaction': (b_myaction, '我自己的动作'),
```

就能 `python3 ~/walle_drive.py myaction` 调用了。

### 你最可能要调的值

| 变量 | 作用 | 调整建议 |
|------|------|----------|
| `TURN_90` | 约 90 度转弯秒数 | 跑一次 patrol,转多了调小,转少了调大 |
| `DEFAULT_DUR` | 基础动作不传参时的秒数 | 按手感调 |
| 各行为里的 `time.sleep(...)` | 每段动作持续时间 | 想快就调小,想慢就调大 |

### 改完后验证语法

```bash
python3 -c "import py_compile; py_compile.compile('/home/walle-2/walle_drive.py')"
```

---

## 五、常见问题

### 登录时一堆 setlocale 警告

无害。Mac 把 locale 传给 Pi,Pi 没装该语言包。消除:

```bash
sudo locale-gen en_US.UTF-8 && sudo dpkg-reconfigure -f noninteractive locales
```

### 调试中途 `Broken pipe` / `closed by remote host`

WiFi 不稳或连接空闲被路由器掐掉。Mac 端已配 `ServerAliveInterval 15` 保活。若 Pi 的 WiFi 省电导致掉线,Pi 上线后可关省电:

```bash
sudo iwconfig wlan0 power off
```

### 免密登录失效(每次要密码)

公钥可能在异常重启中丢失。重新登记:

```bash
ssh-copy-id walle-2@walle-2.local    # 输最后一次密码 raspberry
```

### 树莓派彻底连不上

- 看板子红灯(PWR)亮不亮
- 等 30-60 秒看是否在重启
- `ping walle-2.local` 确认网络

---

## 附录:关键路径速查

| 内容 | 路径 |
|------|------|
| 履带行为脚本 | `~/walle_drive.py`(树莓派) |
| 校准工具 | `~/walle_calibrate.py`(树莓派) |
| 主程序固件 | `~/walle-replica/wall-e/wall-e.ino`(树莓派) |
| 校准固件 | `~/walle-replica/wall-e_calibration/wall-e_calibration.ino`(树莓派) |
| arduino-cli | `~/bin/arduino-cli`(树莓派) |
| Adafruit 库 | `~/Arduino/libraries/Adafruit_PWM_Servo_Driver_Library`(树莓派) |
| 串口设备 | `/dev/ttyACM0` |
| web 服务 | `walle.service`(用 `sudo systemctl stop/start` 控制) |
| 本手册(Mac 本地) | `Downloads/Wall-e/WALL-E-操作手册.md` |
