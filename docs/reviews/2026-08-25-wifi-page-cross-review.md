# WiFi 配置页 + AP 兜底热点 — 交叉评审报告（2026-08-25）

- **Tier / Seats**: L3 三方（claude + codex + built-in agent），双盲，2 轮
- **评审对象**: `web_interface/app.py` 新增 WiFi Configuration section（约 210 行）+ `web_interface/templates/wifi.html`（新文件，168 行）
- **范围排除**: `track_animations.py` 的 300+ 行在途改动与 `docs/track-animations.md`（owner 自有工作，仅确认其中误入字符 `·` 的删除与 Pi 部署版一致）
- **轮次**: Round 1（全量）→ 修复 → Round 2（只审修复）→ 无新 P0/P1，收敛

## Round 1 合并结论（按根因去重）

| # | Finding | 提出方 | 级别 | 处置 |
|---|---|---|---|---|
| F1 | `wifi-sec.key-mgmt open` 非法枚举值，开放网络路径整体失效 | claude P0 + built-in P0 | P0 | **已修**: 空密码时不传 wifi-sec 参数；modify 分支 `remove 802-11-wireless-security` 清遗留。实机验证 `open` 报错原文、`remove` rc=0、空密码 probe profile 无安全属性 |
| F2 | nmcli terse `\:`/`\\` 转义未处理，含冒号 SSID/profile 被静默丢弃 | 三方一致 | P0 | **已修**: `_terse_split()` 统一三处解析。实机验证冒号 profile 入列 |
| F3 | `Thread.start()` 失败时 `_wifi_switch_lock` 永久泄漏 | 三方一致 | P0/P1 | **已修**: try/except 包裹，失败释放锁返回错误 JSON |
| F4 | SSID=热点名会改写兜底热点自身；同名非 WiFi profile 被误改 | 三方一致 | P1 | **已修**: connect 拒绝 `HOTSPOT_PROFILE`；scan 过滤；modify 前 `connection.type` 检查 |
| F5 | status/scan 不捕获 `TimeoutExpired`，500 而非降级 JSON | 三方一致（severity 分歧 2×P1/1×P2，多数决 P1） | P1 | **已修**: `_nmcli` 内部捕获返回 `(124,"","nmcli timeout")`；worker 冗余 except 移除 |
| F6 | modify 分支缺 `autoconnect yes`，重启后不自动连 | codex + built-in | P1 | **已修** |
| F7 | SSID 双重 strip/trim 裁掉合法前后空格 | built-in 单方（L3 采纳） | P1 | **已修**: 服务端仅 strip 判空保留原值；前端去 `.trim()` |
| F8 | 密码经 HTTP 明文传输 | codex 单方 P0 | 裁决 | **记录并升级 owner**: 与全仓既有姿态一致（登录密码同为明文）；最关键场景（连兜底热点配置 WiFi）传输层由热点自身 WPA2 加密保护。TLS 属全应用架构改动，超出本 diff 范围 |

## Round 2 合并结论

| # | Finding | 提出方 | 级别 | 处置 |
|---|---|---|---|---|
| R2-1 | `_terse_split` lookbehind 无法处理"值以反斜杠结尾+分隔冒号"（`Net\\:70`） | 三方独立同报 | P1/P2 | **已修**: 改字符扫描器（escape-pair 敏感），本地 6 用例全 PASS（含 codex 给的 `a\` 失败用例） |
| R2-2 | `remove` 对无 security 的 profile 可能非零、跳过 `connection up` | claude P2 | P2 | **实测反驳**: NM 1.52 上 `remove 802-11-wireless-security` 对无该 setting 的 profile 幂等 rc=0。入 rejection log |
| R2-3 | modify 只查 type，未核对 profile 内保存的 ssid/mode，名字≠SSID 的 profile 会劫持切换 | codex P1 | P1 | **已修**: modify 显式 `802-11-wireless.ssid <ssid>` + `802-11-wireless.mode infrastructure` |
| R2-4 | scan 未过滤隐藏网络占位符 `--`，出现可点击的 `--` 行且点选报误导错误 | built-in P2 | 裁决 P1 | **已修**（moderator 裁决: 属 round-1 过滤完备性的延续，status 已处理 `--` 而 scan 遗漏，一行修复） |
| R2-5 | psk 无 `-` 前缀防护，与 ssid 不对称 | built-in P2 | P2 | **实测反驳**: NM 1.52 上 `connection modify X wifi-sec.key-mgmt wpa-psk wifi-sec.psk -hunter22` rc=0、值正确写入（属性名后的 token 按值解析）。ssid 的 dash 拒绝保留为纯防御 |
| R2-6 | 前端 maxlength=32 按字符、服务端按字节，CJK SSID 输入端放行提交端拒 | built-in P2 | P2 | **记录**: 功能正确仅体验毛刺，错误文案自解释 |

## Round 1 已记录不修的 P2（流程决定）

LC_ALL=C 固定 locale；waitress 线程数与 Rescan 防抖；64-hex PSK 放行；AP 模式下 scan 可用性；超时 kill 只杀 sudo 的孤儿 nmcli；主界面无 /wifi 入口；切换后 8s 单次刷新过早；CSRF token（全仓一致姿态）。

## Rejection Log

| 评审员 | Claim | 证据 | 裁决 |
|---|---|---|---|
| claude R2-2 | `remove 802-11-wireless-security` 对 absent setting 可能非零导致跳过 up | Pi 实测: 建无 security 的 open profile 后 `remove` → rc=0 | **refuted** |
| built-in R2-5 | dash 开头 psk 可能被 nmcli 当选项解析，modify 失败 | Pi 实测: `psk -hunter22`（合法长度）rc=0 且值正确写入；先前 `-foo` 报错系 4 字符长度校验，非 dash | **refuted** |

## 实机验证记录（评审期间与修复后）

1. 登录拦截 302 / 页面 200 / status / scan / 真实 psk 自连 `waikato_iot` 成功（journal: `WiFi switch: connected to waikato_iot`）
2. AP 窗口 150s 存活（journal: `Started Wi-Fi Hotspot` + dnsmasq 池 192.168.4.10-254）、定时器自动恢复、web 服务零重启
3. 热点名 connect 被拒；带冒号 profile 出现在 saved 列表；空密码 probe：profile 无安全属性、up 失败后 NM 自动恢复
4. `_terse_split` 6 边界用例（含反斜杠结尾、转义对、字面 `\:` 值）全 PASS

## 运维附注（非代码评审内容）

- Pi 上曾存 RaspAP 残留全家桶（dnsmasq/hostapd/raspap/dhcpcd + dhcpcd.conf 的 10.3.141.1 静态段）与 NM 双管家冲突，导致首次热点实测 DHCP 端口占用失败；已全部 disable --now，属可逆操作。
- 演示脚本 `~/wifi-demo.sh` 首版误用 `nmcli device disconnect`（会冻结 autoconnect，热点永不自启），已改 `nmcli connection down`。
- **自动兜底路径已实锤**（2026-08-25 15:24，现场演示）：改错密码重启后，NM `policy: auto-activating connection 'WALL-E-Setup'` 自动拉起热点；用户经热点页面填回正确密码，15:27 `WiFi switch: connected to waikato_iot` 完整走通用户故事。
