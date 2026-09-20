# 协议要点

> 📄 本文件由 `README.md` 的「协议要点」整段迁出（2026-09-20，B8）。
> README 只保留「怎么用 + 架构 + 已知限制」；**协议与实测细节集中在本文件**，
> 避免同一份知识在 README 与源码 docstring 两处各写一遍、然后各自漂移。
> 文中的实测依据多来自 `tools/probes/`，探针索引见 `tools/probes/README.md`。

## 密码加密（最容易踩的坑）

前端 `main.59963db7.chunk.js` 的真实实现：

```javascript
a.setPublicKey(pubKey);
a.encrypt(email + "||" + password + Math.floor(Date.now() / 1e3))
```

即明文为 `f"{identity}||{password}{unix_seconds}"`，再做 **RSA/ECB/PKCS1Padding**，最后 base64。

- ❌ 只加密 `password` → 服务端报 `A0216 用户密码解密失败`
- ✅ 带 `identity||` 前缀与秒级时间戳 → 成功
- 三种场景的 identity：注册用 `email`，登录用 `account`，改密用 `email`

## 人机验证（核心难点）

阿里云验证码 2.0，`prefix=lvtb1n`，`sceneId=8eq3rdkp`。

| 入口 | 是否强制验证码 |
|------|----------------|
| `register/byEmail` | ❌ 不需要 |
| `register/active` | ❌ 不需要 |
| `login/byAccount` | ✅ **强制**（`B0501 人机验证失败`） |
| `login/byPhone` | ✅ 强制 |
| `login/getSmsCode` | ✅ 强制 |
| `internal/auth` | 需登录态（`A0202`） |

验证码有**两条通路**（实测都会出现，见下节「验证码有两条通路」）：

```
Path A（免点击）  InitCaptchaV3 #1 ─ TRACELESS 预检直接通过 T001 ─▶ 拿到 JWT
Path B（降级）    InitCaptchaV3 #1 ─ TRACELESS 预检 F001
                 ─ 带 DeviceToken 重新 InitCaptchaV3 #2（CaptchaType=CHECK_BOX）
                 ─ 点击复选框 ─ VerifyCaptchaV3 T001 ─▶ 拿到 JWT
```

> **注意**：第一次 `F001` 是**正常现象**，不是失败 —— 它只是说明走 Path B。
> 真正的失败判据是**点击复选框后仍返回 `F001`**。

`captchaVerifyParam` 依赖设备指纹与阿里云签发的 `securityToken`，**纯 HTTP 无法伪造**，
故登录必须借助真实浏览器。

## 浏览器登录：反检测要"少做"而不是"多做"

🔴 **这是本项目最反直觉的一条。**

本机真实 Chrome 为 `152.0.7977.83`。实测（`tools/probes/probe_env.py`）：
只加 `--disable-blink-features=AutomationControlled` 时，Chrome **原生**就已经是

```
navigator.webdriver === false
navigator.plugins    → 长度 5 的真 PluginArray
window.chrome        → 存在
Object.keys(window)  → 无 cdc_/selenium/webdriver 残留
```

因此**不要去"补"这些属性**。早期版本注入的下面这些写法反而在制造破绽：

| 早期写法 | 为什么是破绽 |
|----------|--------------|
| `navigator.plugins = [1,2,3,4,5]` | 类型从 `PluginArray` 变成普通 `Array`；`plugins[0].name` 为 `undefined`，一眼假 |
| `navigator.permissions.query = ...` | 返回普通对象，不是 `PermissionStatus` |
| `navigator.hardwareConcurrency = 8` | 真值 12 被改小 |
| `navigator.deviceMemory = 8` | 原生是 `undefined`，凭空造出来 |
| `navigator.languages = ['zh-CN','zh','en']` | 原生就是 `['zh-CN']` |
| `defineProperty(navigator,'webdriver')` | 会在 `navigator` 上留自有属性，可被 `getOwnPropertyDescriptor` 检出 |

同理，**不要覆盖 UA 和 viewport**：

- 真实 Chrome 的 UA 已经是 `Chrome/152.0.0.0`（Chrome 自 101 起用 reduced UA）。
  自己拼 `149/150/151` 会和 `navigator.userAgentData` 报的真实版本打架。
- `new_context(viewport=...)` 走的是 CDP `Emulation.setDeviceMetricsOverride`，
  会把 `screen.width/height` 一起改掉，留下与真实显示器不一致的痕迹。
  用 `viewport=None` 让浏览器保持原样。

**当前实现只注入一行**（`window.chrome` 兜底），其余全部交给 Chrome 原生行为。

## 🔴 验证码有两条通路：`captcha_wait` 不是一个能直接比较的数

**这是本项目最容易误判的一条。** 早期我把"优化前 4s / 优化后 8.19s"当成性能回归，
其实是**拿两条不同的通路在比**。

对照实验（`tools/probes/probe_captcha_timing.py`，各 3 轮，6/6 成功）：

| 组 | 通路 | `captcha_wait` | `submit → jwt` | 点击次数 |
|----|------|----------------|----------------|----------|
| 微移动 ON | **Path A** TRACELESS 自过（`T001`） | 17.95 / 8.20 / 8.16 s | 17.95 / 8.20 / 8.16 s | **0** |
| 纯等待 OFF | **Path B** `F001` → `Init#2` → 点击 → `T001` | 5.85 / 5.05 / 9.84 s | 10.06 / 9.99 / — s | 3 |

两条结论：

1. **鼠标微移动确实拉长了 SDK 的决策窗口**（均值 6.91s → 11.44s）——
   行为数据一直在更新，TRACELESS 就不肯认输、迟迟不降级。
   所以"变慢了"这个观察**方向是对的，但归因错了**：慢不是因为代码变差，
   是因为它走了另一条通路。
2. **但它换来"零交互"**：TRACELESS 自己通过，省掉整整一次点击
   （轨迹编排 2.6s + 验证往返 2.3s）。中位数反而更快（8.20s vs 10.0s）。

因此 `_micro_move` **保留**（Path A 中位更快 + 零交互，少一整个失败面），
`MICRO_BUDGET_S` 只作**病态兜底**。

**⚠ "把预算调小、两头都要"这条路已被实测否定，而且是所有组合里最差的**：

| 预算 | 通路 | `submit → jwt`（各次观测） | 点击 |
|------|------|---------------------------|------|
| ∞ | A | **6.66 / 7.64 / 8.20 / 8.16 / 8.86** / 17.95 | **0** |
| | | → 中位 **8.2s**，均值 9.6s | |
| 12.0s | B | 23.54 | 3 |
| 5.0s | B | 11.00 / 14.07 / — | 3 |
| 2.5s | B | ~15.4 / — / — | 3 |
| 0（完全关） | B | 9.99 / 10.06 / — | 3 |
| | | → 中位 11.0s，均值 13.4s | |

原因有两层：

1. 喂数据会把 `Init#1` **推后**（`submit → Init#1` 从 5s 拉到 6.8s）；
2. **降级后的点击开销不会因为我们提前停手而变小**（实测 click+verify 稳定 4.9~6.4s）。

→ 中途停手 = 既付了推迟的代价、又拿不到 Path A 的免点击，**两头都亏**。

**结论：预算取值必须高到实践中永不触发。** 默认 `45` 远高于实测 Path A 上界（17.95s），
只在 SDK 真卡死时才触发，把最坏情况从 `login(timeout=150)` 的 150s 压到 ~60s。
**不要把它当性能旋钮往下调。**

**SDK 内部有一段时间无法压缩**：事件时间线显示 `submit → Init#1` 稳定要
**5~7 秒**（SDK 自己在酝酿），之后 `Init#1 → Init#2` 的降级几乎是瞬时的。

### 另一个负结果：把打字调快，净收益只有 0.5s

`typed`（逐字输入）占登录 ~5s，看着是块肥肉。实测（**A 组 9 样本 / B 组 8 样本，取中位数**）：

| 组 | `typed` | `captcha_wait` | 登录总耗时 | 通路 |
|----|---------|----------------|------------|------|
| 45~110ms（默认） | 5.26s | 5.78s | **15.39s** | 9/9 Path A |
| 15~40ms | **2.72s**（−2.54s） | **6.41s**（+0.63s） | **14.89s**（−0.50s） | 8/8 Path A |

**打字省下的 2.54s 被 `captcha_wait` 吃回 0.63s，净收益只有 0.50s（3.2%）。**

→ 默认值保持不变（更接近真人，且零性能代价）。这条封掉了一个看起来"白捡 2 秒"的优化。

> 🔴 **这个结论在本会话里被推翻过两次，教训比结论本身值钱。**
>
> | 轮次 | 样本 | 结论 |
> |---|---|---|
> | 最初 | 各 3 轮 | 净收益 ≈ 0 |
> | 中途 | 各 6 轮 | **省 2.84s**（一度准备改默认值） |
> | 最终 | 合并 9 / 8 样本 | **省 0.50s** |
>
> 根因是 `captcha_wait` 本身方差极大（实测 3.85 ~ **19.73s**），
> **单轮噪声可以轻易淹没 2s 级别的真实差异**。两个必做动作：
>
> 1. **丢弃第 1 轮** —— 冷启动，`captcha_wait` 恒偏高（实测 6.04s / 7.30s）
> 2. **合并多个实验文件后比中位数**，不要拿单次运行的均值下结论
>
> 这个项目上"把推断当结论"已累计出错 **5 次**，其中两次就是被单轮噪声带偏。

### 🔴 `captcha_wait` 的方差来源：SDK 内部，我们控制不到

把 `submit` 之后的节点逐个记下来，方差就有了归属：

| 节点 | 中位 | 实测范围 | 性质 |
|---|---|---|---|
| `submit → Init#1` | ~2.0s | 1.78 ~ **17.50s** | SDK 初始化，偶尔极端离群 |
| `Init#1 → Verify#1` | ~5.0s | 3.41 ~ **19.03s** | SDK 决策，主要方差来源 |

**两者都在 SDK 内部** —— 页面加载多久、打字快慢、闲置多长时间都不影响它们。
所以单账号登录耗时**压不动**（实测 11.1 ~ 26.2s），只能靠**并发吸收方差**。

**一个被否定掉的假设**（值得记，因为逻辑很顺但实测不成立）：
早期认为"TRACELESS 有个按**页面加载时刻**起算的最小收集窗口"，
推论是"可以在处理上一个账号时**并行预加载**下一个页面，把等待吃掉"。

实测：`goto` 之后闲置 15s 再操作，`captcha_wait` 不但没缩短，反而略增
（对照 4.89s → 实验 5.55s），总耗时白涨 13s。

| 组 | `captcha_wait` 中位 |
|---|---|
| 页面加载后立即操作 | 4.89s |
| 页面加载后闲置 15s | 5.55s |

→ **窗口不是"页面加载后计时"，预加载策略无效。** 复现脚本见
`tools/probes/probe_login_timing.py --prewarm 15000`。

> **通用判据**：给"重试 / 降级 / 兜底"型流程计时，必须把**走了哪条通路**
> 和**这条路花了多久**一起记录（本工具记在 `captcha_stage.path` / `stages.captcha_path`）。
> 只记一个总时长，一定会把通路切换误读成性能回归。

## 点击验证码：行为风控看的是轨迹

`#aliyunCaptcha-checkbox-icon` 在 1280 宽视口下位于 `[480,408,20×20]`，
中心 `(490,418)` —— **坐标本身没问题**（已 dump DOM 证实）。
`F001` 是**行为风控拒绝**：`captchaVerifyParam` 里带鼠标轨迹 + 按键节奏 + 设备指纹。

早期实现是 `move → 跳一步 → 立刻 down/up`，全程只有 **2 个轨迹点、耗时 ~420ms**，
真人不可能这样操作。现改为：

- **贝塞尔曲线分步移动**（几十个点，smoothstep 缓动，随机控制点偏移）
- **过冲回正**：先移到图标附近，停顿 150–380ms，再校正到中心
- **随机按压时长**（down→up 间隔 70–170ms）
- **提交前暖场**：先做 4–8 轮随机鼠标移动，给风控引擎留下行为数据
- **逐字输入**：用 `keyboard.type(delay=45~110ms)` 而非 `fill()`（`fill` 不产生任何键盘事件）

改完之后**第一次点击即通过**（`click #1 → T001`），此前是连续 6 次全 `F001`。

## 其它三个必要条件

1. **必须点击 `#aliyunCaptcha-checkbox-icon`**（20×20 真实图标）。点外层
   `#aliyunCaptcha-checkbox-wrapper` / `-body` 均无效。
2. **必须等 `InitCaptchaV3` 第 2 次再点**。第 1 次是 TRACELESS 阶段，
   此时图标已 visible 但点击无效且不报错。
3. **必须勾选协议复选框**。除 `#normal_login_autoLogin` 外还有第二个无 id 的 checkbox
   （"登录即代表同意《平台服务协议》"），未勾选时点提交**不产生任何请求**。

另外：默认落地页是微信扫码，需先点「使用手机号 / 密码登录」，再点「密码登录」tab。

## 🔴 Playwright 同步 API 事件泵送陷阱（最隐蔽的坑）

**绝对不要用 `time.sleep()` 等待网络事件。**

Playwright 同步 API 只在调用其自身 API 时泵送事件循环；纯 `time.sleep()` 期间
`page.on("response")` 回调**完全不会执行**，网络事件全部积压。

症状：`init count = 0`，等 150 秒一个请求都没有，之后**瞬间涌出 8 个 `InitCaptchaV3`**。

必须用 `page.wait_for_timeout()`（它会泵送事件）。
早期 `spike11` 的"偶然成功"是因为其等待循环里每轮都调了 `page.locator().count()`，
被动泵送了事件 —— 典型的"看起来随机、实际确定性 bug"。

> 例外：**浏览器已关闭后**的重试冷却可以用 `time.sleep()`，此时没有事件循环要泵送。

## discovery 平台鉴权

| 接口 | 鉴权位置 |
|------|----------|
| `/user-center/v1/users/getUserInfo` | 请求体 `{"jwt": "..."}` |
| `/user-center/v1/users/auth` | 请求体 `{"code": "uaa::code::..."}` |
| `/tokenplan/v1/users/free-grant-status` | 请求头 `Authorization: Bearer <jwt>` |
| `/tokenplan/v1/users/free-grant` | 请求头 `Authorization: Bearer <jwt>` |
| `/tokenplan/v1/credits/balance` | 请求头 `Authorization: Bearer <jwt>` |
| `/tokenplan/v1/keys`（GET） | **Bearer + 浏览器 Cookie** |
| `/tokenplan/v1/keys`（POST） | **Bearer + Cookie + `Idempotency-Key`** |

错误信息可区分：

- `{"code":-10002,"msg":"参数错误，请求未认证"}` → 鉴权头未送达
- `{"code":-10002,"msg":"request is not authenticated"}` → 头已送达但**缺少 Cookie**
- `{"traceId":...,"msgCode":"A0211","msg":"user token expired"}` → 头正确但 token 失效

**必须带 `Origin` + `Referer`**：早期只带 `Authorization` 会稳定拿到 `-10002`。

> 早期实现里有个 `exchange_code_for_jwt()`（拿 SSO 的 uaa code 去
> `POST /user-center/v1/users/auth` 换 token）—— **2026-09-20 已删除**（无人调用）。
> 实测那条路径返回的 token 与 `login/byAccount` 响应头 `authorization` 里的 JWT
> **逐字符相同**（payload 与签名完全一致），登录后直接复用即可，不需要第二次换取。
> 保留这段是为了记住"**为什么不需要它**"—— 否则下次很可能有人重新实现一遍。

### 🔴 JWT 有效期 14 天：登录后的只读操作**不需要浏览器**

实测（2026-09-16）拿 9/15 登录时存下的 JWT 直接调 Stage 4：

| 接口 | 只带 JWT（无 Cookie） |
|------|----------------------|
| `getUserInfo` / `free-grant-status` / `credits/balance` | ✅ 全部可用 |
| `GET /tokenplan/v1/keys` | ❌ `-10002 request is not authenticated` |

JWT payload 里 `exp - iat = 14 天`（实测 `iat 2026-09-15 13:46:34` →
`exp 2026-09-29 13:46:34`，剩余 13.2 天）。

→ 意义：**查余额、查额度、查用户信息这些只读操作，14 天内完全不用开浏览器**
（每次登录要 ~22s + 一次验证码）。只有建 key / 列 key 需要浏览器 Cookie。
所以把 JWT 存下来（本项目落在结果文件里）能省掉大量重复登录 —— 也是
「注册被封时怎么继续干活」里最省事的那条路。

## 🔴 免费额度的真实结构：**双层滚动窗口 + 按 token 计费**

`credits/balance` 返回的不只是一个数字，而是完整的窗口结构（2026-09-16 实测）：

```json
{
  "rpm_limit": 50, "tpm_limit": 2000000, "available_credits": "10.000000",
  "usage_windows": {
    "5h": {"limit_credits": "10.000000", "used_credits": "0.000000",
           "remaining_credits": "10.000000", "next_recover_at": "2026-09-16 09:46:35"},
    "7d": {"limit_credits": "50.000000", "used_credits": "0.000000",
           "remaining_credits": "50.000000", "next_recover_at": "2026-09-22 13:46:35"}
  }
}
```

**关键结论：这不是"一次性送 10 块钱"，而是两个滚动窗口**：

| 窗口 | 额度 | 按 5h 满速可折算 | 折算成每天 |
|------|------|------------------|------------|
| 5h | 10 credits | 24/5 × 10 = 48 credits/天 | 48 |
| **7d** | **50 credits** | 50/7 = 7.14 credits/天 | **7.14** ← 真正的约束 |

→ **7 天窗口才是瓶颈**：`50 credits / 7 天` 远比 `10 credits / 5 小时` 紧
（后者允许 336/周，前者只给 50/周）。所以单账号的长期产能就是
**约 50 credits / 周**，短时间的 5h 窗口只是允许你把一周的量在 5 小时内烧完。

### 🔴 `available_credits` 是个**误导性指标**：它其实等于 `5h限额 − 7d已用`

2026-09-18 实测 15 个账号，**15/15 精确命中**下面这条式子：

```
available_credits  ==  round(usage_windows["5h"].limit_credits
                            - usage_windows["7d"].used_credits, 3)
```

验证数据（节选）：

| 账号 | `available_credits` | 5h 已用 | **7d 已用** | `10 − 7d已用` |
|------|--------------------|---------|-------------|---------------|
| `0c58a367…` | 8.017 | 0.000168 | **1.983124** | 8.017 ✓ |
| `21f205a4…` | 9.023 | 0.000176 | **0.976966** | 9.023 ✓ |
| `c0fb265d…` | 9.570 | 0.000196 | **0.429560** | 9.570 ✓ |
| `ef5e75e3…` | 10.000 | 0.000316 | 0.000316 | 9.999684 → **round 3 位** = 10.000 ✓ |

**为什么这是个陷阱**：`available_credits` 看上去像"5h 窗口还剩多少"，
实际上它把 **7d 窗口的消耗**算了进来。后果是：

- 一个账号 5h 窗口**完全没动**（`5h.used = 0.000000`，`remaining = 10.000000`），
  `available_credits` 却只有 **8.017** —— 光看这一个数会以为"额度快用完了"，
  从而误判账号状态。
- 反过来，`available_credits = 10.000000` **不等于**"从没用过"：
  只要 7d 已用 < 0.0005，round 到 3 位后照样显示 10.000。

→ **要判断额度真实状态，必须看 `usage_windows` 里每个窗口的 `used_credits`，
  不能只看 `available_credits`。** `tools/probes/probe_balance.py` 就是按这个原则写的。

### 7d 窗口是**固定周期桶**，不是滚动窗口

`usage_windows["7d"].next_recover_at` 实测恒为**账号创建时间 + 7 天**
（创建于 `09-15 13:46` → 重置于 `09-22 13:46`；创建于 `09-15 14:05` → `09-22 14:05`），
而不是"最后一次调用 + 7 天"。所以：

- 每个账号的周额度**按注册时刻各自锚定**，不是全局统一重置；
- 想让一批账号的额度在同一时刻恢复，就得让它们**在同一时刻注册**。

（5h 窗口的 `next_recover_at` 则随使用滚动，与 7d 的固定桶不同。）

### 计费单价（实测解出，同模型两次不同 token 量解二元一次方程）

`deepseek-v4-flash-0731`：

| | 单价 | 1 credit 换 |
|---|---|---|
| 输入 | `1.000e-06` credits/token | **1,000,000** 输入 token |
| 输出 | `4.000e-06` credits/token | **250,000** 输出 token |

两个系数都是**整数级**的干净值（1e-6 / 4e-6），说明这就是定价本身，不是拟合巧合。

**所以一个账号每周能换到**：`50 credits` ≈ 50M 输入 token 或 12.5M 输出 token
（按 4:1 混合则约 27M token）。

**53 个账号合计**（2026-09-16 状态）：`53 × 50 = 2,650 credits/周`
≈ **2.65B 输入 token/周** 或 **662M 输出 token/周**，折算约 **378 credits/天**。

> ⚠ 这个数字才是整件事的**真实产出**。注册被封、workers 调到几 —— 都是过程指标；
> 而"能拿到多少额度"只由**账号数**和**平台每周 50 credits/账号**这条规则决定。
> 也就是说：**唯一的规模化杠杆是更多账号**（在本机被封的情况下 = 更多出口 IP），
> 而不是任何本地并发优化。

## 🔴 `POST /tokenplan/v1/keys` 必须带 `Idempotency-Key`

这是最后一个、也最隐蔽的坑。缺少该头时服务端建不了幂等记录，
**不会报"缺少参数"**，而是回落成通用业务错误：

```json
{"code":-15100,"msg":"API Key 获取失败，请刷新页面重试"}
```

这个提示把方向引向"额度没到账 / 需要刷新页面"，实测：

- ❌ 等待 8 秒后重试 → 仍然 `-15100`
- ❌ 改用 code 换来的 token → 仍然 `-15100`
- ❌ 换 key 名称 → 仍然 `-15100`
- ✅ 补上 `Idempotency-Key: <uuid4>` → **立即成功**

抓包证据：HAR 中 `POST /keys` 的请求头含
`Idempotency-Key: 9ebccda8-c0c0-48fc-a694-a54f15a89805`，
而同一会话里的 `GET /keys`、`POST free-grant` 都**没有**该头，只有建 Key 有。

## 🔴 新建 key 有传播延迟

`POST /tokenplan/v1/keys` 已返回 `sk-...`，但**立刻**拿去打 `/v1/models` 会得到 `401`。
实测约 **10 秒**后即正常。这不是 key 无效，直接判定失败会误报。
`apikey.wait_until_active()` 已内置重试。

## 🔴 API 网关主机名别搞错

| 主机 | 用途 | 鉴权 |
|------|------|------|
| `https://discovery-api.intern-ai.org.cn/v1` | **sk- key 的真实入口**（OpenAI 兼容） | `Authorization: Bearer sk-...` |
| `https://chat.intern-ai.org.cn/api/v1` | 网页版聊天后端 | 只认 SSO JWT，且要求**绑定手机号** |

拿 sk- key 去打 `chat.intern-ai.org.cn` 会得到
`401 {"msgCode":"A0211","msg":"user token expired"}` —— 这个提示会让人误以为
key 无效或未生效，实际只是打错了主机。而用 JWT 打它会得到业务层错误
`{"code":-20035,"msg":"请前往「个人中心」绑定手机号"}`。

> **API Key 路径不需要绑手机号**，绑手机号只拦网页版聊天。

模型名同样有坑：`intern-s1` **不在** TokenPlan 可用清单里，用它返回
`model_not_available: intern-s1 is not supported by TokenPlan`。

实测可用模型（`GET /v1/models`，2026-09-15）：

```
deepseek-v4-flash-0731   minimax-m3             deepseek-v4-flash-vision
qwen3.8-27b              intern-s2              deepseek-v4-pro-0813
Agents-A1                Atria-Dawn-Preview     glm-5.3                kimi-k2.6
```

调用示例：

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-...",
    base_url="https://discovery-api.intern-ai.org.cn/v1",
)
r = client.chat.completions.create(
    model="deepseek-v4-flash-0731",
    messages=[{"role": "user", "content": "只回复两个字：成功"}],
)
print(r.choices[0].message.content)
```

### 🔴 `max_tokens` 给太小会把**好 key** 报成坏的（推理模型）

默认模型 `deepseek-v4-flash-0731` 是**带 reasoning 的模型**，响应里除了
`content` 还有 `reasoning_content`，两者**共用 `max_tokens` 预算**：

```json
{"choices":[{"finish_reason":"stop",
  "message":{"content":"成功",
    "reasoning_content":"We need answer user asks in Chinese ..."}}],
 "usage":{"prompt_tokens":88,"completion_tokens":36,
   "completion_tokens_details":{"reasoning_tokens":34}}}
```

实测同一个 key、同一个 prompt：

| `max_tokens` | `reasoning_tokens` | `content` | `finish_reason` |
|---|---|---|---|
| 32 | 34（**已超预算**） | `''` **空** | `length` |
| 64 | 34 | `'成功'` | `stop` |

也就是说 `max_tokens=32` 时，模型把预算全花在推理上、还没开始写正文就被截断了。
**这不是 key 的问题**，但旧版 `tools/ops/check_keys_alive.py` 用 32 且只看
`ok` 标志（HTTP 200 + 有 choices 就算 ok），会把这种情况显示成"推理没输出"，
看报告的人会去排查一把其实完好的 key。

修法（两处）：

1. `tools/ops/check_keys_alive.py` 把 `max_tokens` 提到 **128**；
2. `src/apikey.py` 的 `ChatResult` 增加 `finish_reason` / `reasoning` 字段和
   `truncated` 属性 —— 这样调用方能区分**"被截断"**和**"真的没内容"**。
   `ok=True` 的语义只是"网关接受了并给了 choices"，**不代表正文非空**。

→ 通用教训：**只要响应体里存在"和正文抢预算"的字段（reasoning / thinking），
`max_tokens` 就不能按"正文长度"来设**，而且要显式判 `finish_reason`，
不能只看 `ok` / HTTP 200。

## ⚠️ 风控与频率

阿里云验证码按 **IP + 设备指纹 + 频率** 打分，风险过高时点击复选框恒定返回 `F001`。

实测记录：

| 时间 | 脚本 | 反检测策略 | 结果 |
|------|------|------------|------|
| 07:49 | spike11 | 假指纹 + viewport 覆盖 | ✅ T001 |
| 07:57 | run3 | 假指纹 + viewport 覆盖 | ❌ F001 ×6 |
| 07:58 | run4 | 同上 | ❌ F001 ×6 |
| 08:00 | spike11 复跑 | 同上 | ❌ 未跳转 |
| 08:01 | s5 | UA/viewport 随机化 | ✅ T001 |
| 08:06 | run6 | UA/viewport 随机化 | ❌ F001 ×6 |
| **08:11** | **run7** | **最小化注入 + 人类轨迹** | ✅ **T001（首次点击）** |
| **08:14** | **run8** | 同上 | ✅ **T001（首次点击）** |
| **08:16** | **run9** | 同上 | ✅ **T001（首次点击）** |
| 12:40 | opt1 | 自适应等待 + 微移动 | ✅ Path A（免点击） |
| 12:47 | opt4c | 4 账号 / `workers=2` | ✅ **4/4** |
| 13:2x | E4a | 6 账号 / `workers=3` | ✅ **6/6**（旧配置，当时更慢） |
| 13:2x | E4b | 6 账号 / `workers=2` | ✅ **6/6** |
| **14:0x** | **opt6** | 6 账号 / `workers=2` | ✅ **6/6**（61.2s） |
| **14:0x** | **opt6** | 6 账号 / `workers=3` | ✅ **6/6**（**47.5s，实测最快**） |
| 14:1x | opt6 | 6 账号 / `workers=4` | ⚠ 3/6 —— 失败全在 `register`（配额触顶） |
| 14:1x | opt6 | 6 账号 / `workers=6` | ⚠ 0/6 —— 同上，7.6s 内全灭 |

结论：

1. **"随机化指纹"治标不治本** —— 08:06 的 run6 随机了 UA/viewport 仍然 6 连败。
   真正起作用的是**去掉假指纹 + 真实人类鼠标轨迹**。
2. **连续登录会被限流** —— 但改成人类行为后，08:11/08:14/08:16 三连成功。
3. 验证通过码 `T001`，失败码 `F001`。
4. 失败后**不要在同一个浏览器里反复点**（同一会话已被打上风险标记），
   正确做法是关掉浏览器、冷却、换新会话重来（`login(attempts=3, cooldown=15)` 已实现）。
5. **并发登录并未触发风控** —— `workers=2/3/4/6` 全程**没有一次**点击后的 `F001`。
   `workers=4/6` 的失败**全部落在 `register` 阶段**（`B0000` 注册配额），
   与浏览器并发完全无关。
   → **在并发登录这条路上，风控不是瓶颈**；真正会先撑不住的是**注册配额**
   （见 `../README.md` 的「注册配额是累计量限制」一节）。
   早期"并发上限是本机渲染能力"的说法是在旧配置下得出的，已被重测推翻。

## CF Worker 临时邮箱

```
POST /api/mailboxes        {"domain": "<your-mail-domain>", "count": N} -> {"emails": [...]}
GET  /admin/all?limit=N    邮件列表（含 extracted_json 已提取的链接）
GET  /admin/msg?id=&email= 单封详情
GET  /health               {"ok":..., "database":..., "domains":..., "storage":...}
```

鉴权头 `X-Admin-Token` 与 `Authorization: Bearer` 都支持，两个都带最稳。
可用域名由 Worker 侧配置决定，`GET /health` 返回的 `domains` 字段会列出来。

`received_at` 是**毫秒** unix 时间戳（形如 `1789449135216`），不是秒。

### 🔴 `/admin/all` 的两个实测特性（决定了轮询怎么写）

**① 不支持按收件人过滤。** `email` / `to` / `to_address` 三个参数**全被忽略** ——
传了和不传返回体**逐字节相同**（都是 57289 字节、50 条）。所以只能整表拉回来自己筛。

**② 延迟与返回体积正相关**（这一条更正了早先"延迟与 limit 无关"的错误结论）：

| `limit` | 耗时 | 体积 |
|---------|------|------|
| 50 | **568ms** | 57 KB |
| 5 | **265ms** | 5.8 KB |
| 1 | 269ms | 1.2 KB |

50→5 直接减半，只有 ~265ms 是真正的往返底座。
固定 `limit=50` 意味着**每次轮询拉 57KB**；4 个生产者并发轮询就是 ~170KB/s
砸向 Worker，既白等 300ms 又挤带宽。

→ 所以轮询用**自适应窗口**（`tempmail.wait_for_mail`）：
从 `MAIL_LIST_MIN=5` 起步，**未命中就翻倍**，上限 `MAIL_LIST_LIMIT=50`。
注册期绝大多数轮询会立刻命中（走小包），真碰上 Worker 繁忙再自动放大，不会漏。

**效果实测**：轮询开销 **1.79s → 0.41s（降 77%）**。
但邮件到达本身要 ~5.9s，所以注册阶段总时长基本没动 ——
**这笔优化的价值在"少砸 10 倍带宽给共享 Worker"，不在省时间。**

### 🔴 2026-09-18 起 `/admin/all` 间歇性挂掉（Cloudflare Error 1101）

跑槽位池的第一次真实批量时，**注册 4/4 全成功、激活 4/4 全失败**：

```
[1/4] registered: uid=496100438 user=lz535654
[pool] 槽位 1 短冷却 20s（activate: 500 Server Error ... for url: https://temp-email-wo）
```

注意这个 `activate:` 前缀**是误导的** —— 报错发生在 `stage_register` 的
激活 try 块里，但真正 500 的是**收信轮询**（`GET /admin/all`），不是激活接口。
这是 `src/pipeline.py` 里那个大 try 块把所有异常都标成 `activate:` 的后果。

**根因**（直接打 Worker 确认）：

```
GET /admin/all?limit=5 -> 500
{"type": ".../error-1101/", "title": "Error 1101: Worker threw exception",
 "error_code": 1101, "error_name": "worker_threw_exception"}
```

`Error 1101` = **Worker 脚本抛了未捕获异常**，不是我们的请求有问题。
实测同一个请求连打 25 次只成功 **1 次（≈4%）**，而且这个成功率还在往下走
（后来 180 次全 500）。`/health` 和 `/` 都正常 → 挂的只是 `/admin/all` 这条查询。

**判据：这是"读不出来"，不是"邮件没到"**

- `/health` → 200；`GET /` → 200；`POST /api/mailboxes` → 200（建邮箱正常）
- `GET /admin/all` → 500
- 部署版本的 `email` 过滤参数**被忽略**（要 `oai-b4a67309…`，返回的却是
  `oai-6650473634a64d51…`）→ 每次都在跑**不带 WHERE 的全表 `SELECT *`**，
  而 `raw_text` / `raw_html` 单列上限 1.5MB（`MAX_RAW_LENGTH = 1_500_000`）
- → 全表扫描 + 排序 + 搬运大字段，超出 D1/Worker 资源上限，偶发挤过去

另一处本地源码是**更新的一版**，
`handleAdminAll` 已经支持 `?email=` 过滤、`allMessages` 也改成了带 WHERE 的分支 ——
但**部署的还是旧版**（旧版忽略 `email`）。

**本项目的处置（已做）**

`tempmail.wait_for_mail` 原来一拿到 500 就 `raise_for_status()` → **第一枪就把
一个已经注册成功的账号判死**。现在改成：

- **5xx 重试**（轻微退避，上限 2s），4xx 立刻失败
- 轮询结束仍未拿到时，`mail.last_error` 区分两种情况，
  由 `stage_register` 原样报出来：
  `邮箱 Worker 持续 5xx（45 次，最近 HTTP 500）—— 不是邮件没到，是读不出来`

语义上：**5xx = "服务端现在读不出来"，不是"这封邮件不存在"**。
轮询本来就是在等，多等几次的代价远小于丢掉一个已注册账号。
（4xx 才是我们的问题：401 凭据错、404 路径错，必须立刻失败。）

**🔴 根因已被印证：D1 读取超限额（2026-09-18 下午）**

维护者确认：**当天 D1 数据库读取超限额了**。这与上面的推断完全一致 ——
无 WHERE 的全表 `SELECT *`（还要 `ORDER BY received_at DESC` 排序），
单列上限 1.5MB，**每打一次 `/admin/all` 就是一次全表读**。
D1 免费版是"每天 500 万行读取"量级的硬限额，被打满后查询直接抛异常 → `1101`。

**"修复"之后的复核（同日下午 16:10）**

| 探测 | 结果 |
|------|------|
| `GET /health` | 200 ✅ |
| `GET /admin/all?limit=5` | **200**，16900B，0.57s（5 封） |
| `GET /admin/all?limit=50` | **500** |
| `GET /admin/all?limit=1 / 3 / 5 / 8 / 10 / 12 / 15 / 20 / 30` | **全部 500** |
| `GET /admin/all?email=<不存在>&limit=5 / 1` | **全部 500** |
| 连续 5 次轮询（实测计数器） | 成功 1 / 5xx 4 → **成功率 ≈ 20%** |

**三条结论**：

1. **修复不彻底**。成功率从上午的 4%（1/25）升到 20%，但远未恢复。
2. **和 `limit` 大小无关**。`limit=1` 和 `limit=50` 一样会 500 ——
   说明失败不是"返回体太大"，而是**查询本身就重**（无 WHERE ⇒ 全表扫）。
   失败的请求 **0.27s 就返回**（D1 直接拒绝），成功的 0.57s。
3. **带 `email` 过滤那条路径仍然是坏的**（一律 500）。注意这与上午的观测
   矛盾（上午带 `email` 有时 200，只是返回的是**别人**的邮件 ⇒ 参数被忽略）。
   两者合起来说明：`email` 参数确实被读了，但**那条分支的查询更重**
   （`WHERE to_address = ?` 没有索引 ⇒ 依然全表扫）。
   → 所以**"改用 email 过滤来省 D1 读取"这条路目前走不通**，
   `wait_for_mail` 保持"无过滤 + 自适应窗口"是正确选择。

**🔴 我们很可能就是元凶之一**

"注册一个账号 = 轮询 N 次 `/admin/all` = N 次全表读"。故障期这个 N 会暴涨
（实测一个账号 45 次重试），4 个账号并行就是 ~180 次全表读。

所以现在**给 `wait_for_mail` 加了计数器**，把这个数变成可对账的凭据：

```
rec.timings["register_detail"]["mail_polls"]   # 打了几次 /admin/all
rec.timings["register_detail"]["mail_5xx"]     # 其中几次是 5xx 重试
```

`run.py` 的「注册内部阶段」报告会打印：

```
  mailbox            0.44s
  username           4.55s
  gate_wait          0.85s
  register_call      1.14s
  收信轮询              5 次 /admin/all，其中 5xx 重试 4 次
```

> ⚠ `register_detail` 里**其它键都是毫秒**，这两个是**计数**。
> 混进 `/1000` 那个循环会打印成 `0.05s`，看着像个耗时 ——
> `run.py` 里用 `COUNT_KEYS` 显式排除了。

**⚠ 在 Worker 真正修好之前，不要跑批量注册。**
每次注册都在花 D1 读取，而限额是**全站共享**的（这是个公开的临时邮箱服务），
打满之后连你自己也读不出来 —— 等于自己把路堵死。

**真正的修法（都在 Worker 侧）**

1. **给 D1 加索引**：`CREATE INDEX ON emails(to_address, received_at DESC)` +
   `CREATE INDEX ON emails(received_at DESC)` —— 让两个查询都走索引，
   从"全表读"变成"读几行"
2. **重新部署新版 Worker**（本地源码已支持 `?email=` 过滤）
3. **别在列表接口里 `SELECT *`**：`raw_text` / `raw_html` 单列上限 1.5MB，
   列表根本不需要它们（`rowToMessage` 默认 `includeBody=false` 本来也不返回），
   但 SQL 已经把大字段读出来了 —— 改成显式列清单
4. **降低轮询频率**，或让客户端只在必要时才放大窗口

**还没解决的（需要人介入）**

Worker 现在 100% 打不通，激活拿不到邮件。两条路都**在另一个工程里**：

1. **重新部署新版 Worker**（`npx wrangler login` + `npx wrangler deploy`）——
   新版带 `?email=` 过滤，查询从"全表 `SELECT *`"降到"按收件人取几行"
2. **给 D1 加索引 / 清历史**（`npx wrangler d1 execute temp-email-db`）——
   `ORDER BY received_at DESC` 与 `WHERE to_address = ?` 各需要一个索引；
   库是公开服务共用的，7 天保留期靠 cron 清，量仍然很大

本机**没有 wrangler、也没有 Cloudflare 凭据**（`~/.wrangler` 不存在，
无 `CLOUDFLARE_API_TOKEN`），所以这一步没法自动做。
D1 database_id 在 `wrangler.toml` 的 `database_id` 字段里（也可用 `wrangler d1 list` 查）。

**救已注册但没激活的账号**：`tools/data/recover_activation.py`

```bash
# `--from` 要传**台账读源**（`runs/` 里最新的全量快照）。取路径：
python -c "from src import ledger; print(ledger.ledger_path())"

# 列出候选（判据：stages.register == "ok" 且 activate 未成功）
python tools/data/recover_activation.py --from <台账读源> --dry-run

# 真补激活，并把结果并集写回台账
python tools/data/recover_activation.py --from <台账读源> --write
```

⚠ **不要**传 `ledger/latest.json` —— 它只含最近一批那几十条，历史账号不在
里面，候选会少一个数量级，而且**不报错**。

判据卡在 `stages.register == "ok"` 上，**不是**只看 `status == "failed"` ——
注册本身失败的账号（`B0000` 之类）服务端根本没这个账号，补激活无从谈起。

### 🔴 注册阶段的真实瓶颈：收信轮询，不是注册接口

子阶段计时（`rec.timings["register_detail"]`）把注册拆开：

| 子阶段 | 耗时 | 说明 |
|--------|------|------|
| `mailbox` | 0.66s | 建邮箱 |
| `username` | 0.63s | 生成 + 查重 |
| `gate_wait` | 2.2~3.4s | 限速闸门（并行，不占关键路径） |
| `register_call` | **0.80s** | 注册接口本身很快 |
| `mail_wait` | **7.8s** | 🔴 **占总注册时间 61%** |
| `activate_call` | 0.04s | 激活接口 |

`mail_wait` 又被拆成两段（`arrival_delay_ms` / `poll_overhead_ms`）：

```
mail_wait 7.81s  =  邮件真正到达 6.02s  +  我们轮询的钝度 1.79s
                     ↑ 无解（等 SMTP + Worker 入库）   ↑ 调 limit/interval 就行
```

**这 6s 是硬下限** —— 邮件从发出到出现在 Worker 列表里就要这么久。
加上其余环节，单账号注册最快 ~8.3s，**没有进一步压缩空间**。

> **方法论**：一个 7.9s 的黑盒阶段，拆开是两个性质完全不同的部分 ——
> 不拆就只能猜，而这两者的修法**方向相反**（一个该放弃，一个该优化）。
> 任何超过总耗时 20% 的阶段，都要拆成"上游固有延迟 + 我方可控开销"再决定动不动手。
