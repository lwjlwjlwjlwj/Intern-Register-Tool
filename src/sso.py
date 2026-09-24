"""OpenXLab SSO 客户端 —— 注册、激活、登录（纯 HTTP 部分）。

接口清单（均以 /gw/uaa-be 为前缀）：
  POST /api/v1/register/check            {"item": email, "type": "email"} -> {"exist": bool}
  POST /api/v1/personal/username/check   {"username": "..."}              -> bool（true=可用）
  POST /api/v1/cipher/getPubKey          {"type": "register", "from": "browser"}
  POST /api/v1/register/byEmail          {"username","email","password","source","clientId"}
  POST /api/v1/register/active           {"token": "...", "sign": "..."}  （body 即 URL query 对象）
  POST /api/v1/login/byAccount           {"account","password","autoLogin"}  ← 强制人机验证
  POST /api/v1/internal/auth             {"clientId": "..."} -> {"code": "uaa::code::xxx"}

关键结论：
  - **注册/激活不需要人机验证**（失败时报 A0216 密码解密失败，而非 B0501 人机验证失败）
  - **登录强制人机验证**，纯 HTTP 无解，必须走 `src/browser/`（浏览器登录子包）
  - 密码字段 = RSA_PKCS1v15(f"{identity}||{password}{unix_ts}") 的 base64

🔴 429 限流的真实边界（2026-09-15 实测，见 tools/probes/probe_429.py）：
  - `personal/username/check`（只读）：**8 路并发也完全不限流**
  - `register/byEmail`（写）：**4 路并发时 3 路被 429 拒绝**，且是立即拒绝（~1.2s）
  所以限流挂在**写操作**上，不是笼统的 IP 突发限速。
  429 是限流信号而非业务错误 —— 必须退避重试，不能当注册失败处理。
  本项目实测：加退避重试 + 注册并发降到 2 之后可稳定跑通。
"""

import random
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from . import config
from .crypto_rsa import encrypt_password


def sso_host_for_cookie(gw: str) -> str:
    """从 SSO 网关 URL 提取 cookie domain（形如 sso.openxlab.org.cn）。"""
    host = urlparse(gw).hostname
    return host or "sso.openxlab.org.cn"

# ── 阿里云 WAF（acw_sc__v2 挑战）穿越 ─────────────────────────────
# 2026-09-24 起 sso.openxlab.org.cn 网关前挂了阿里云 WAF：纯 requests
# 直连（无 JS 能力）会被 200 + text/html 挑战页拦截（特征 aliyunwaf /
# acw_sc__v2），注册/激活全部假失败。
#
# 🔴 挑战页有有效期：响应里的 `arg1` 每次都不同，服务端只认"刚发的这次
#    挑战"的答案 —— 用旧挑战页算出的 cookie（哪怕算法完全正确）会被
#    判无效并重新挑战。所以必须**用刚被拦下的那页**现抓现算。
#
# 🔴 WAF 对写请求有三种响应，且会随 IP 热度轮换出现，必须全部处理：
#   · 200 + 挑战页（aliyunwaf 特征）→ 算 cookie 注入后重试
#   · 405 + data-spm 拦截页（无 JS 挑战，纯 requests 无从下手）
#     → 用"长得不同"的变体路径（大小写 / api/v2）诱导同一会话再战，
#       变体路径不在精确命中自定义规则的范围里，会落到默认挑战通道
#   · 429 / 5xx → 业务侧限流，退避重试
#
# 解法：requests 拿到挑战页后，把页面 HTML 喂给共享的 Playwright 无头
# 浏览器（route 拦截让它以 sso 域加载、原生 JS 算 cookie），读出
# acw_sc__v2 注入 requests session 重试。浏览器进程全进程只起一个
# （每次只换 context，~100ms 而不是 ~2.5s 冷启动），所有调用用锁串行。
_WAF_LOCK = threading.RLock()
_WAF_SHARED = None  # (playwright, browser) 惰性单例


def _waf_shared():
    """惰性创建全进程共享的无头浏览器。"""
    global _WAF_SHARED
    if _WAF_SHARED is None:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True,
                                     executable_path=config.CHROME_PATH)
        _WAF_SHARED = (pw, browser)
    return _WAF_SHARED


def _reset_waf_shared() -> None:
    """关掉并清空共享浏览器（崩溃后下次调用重建）。"""
    global _WAF_SHARED
    shared, _WAF_SHARED = _WAF_SHARED, None
    if shared:
        try:
            shared[1].close()
        except Exception:
            pass
        try:
            shared[0].stop()
        except Exception:
            pass


def _compute_waf_cookie(challenge_html: str) -> str | None:
    """用无头浏览器执行**当前**挑战页 JS，返回 acw_sc__v2 的值。

    🔴 不跨会话缓存：挑战页的 arg1 每次不同、cookie 绑定"生成它的那次
    挑战"（acw_tc 会话）。跨请求/跨客户端复用旧 cookie 会被 WAF 判无效
    并重新挑战。因此每个 SSOClient 被拦时都拿**自己的**挑战页现算，
    算出的 cookie 只在自己的 session（同 acw_tc 会话）内自动生效。
    """
    if not challenge_html or "acw_sc__v2" not in challenge_html:
        return None
    try:
        with _WAF_LOCK:
            _, browser = _waf_shared()
            ctx = browser.new_context(user_agent=config.USER_AGENT)
            try:
                page = ctx.new_page()
                served = {"n": 0}

                def fulfill(route):
                    served["n"] += 1
                    body = challenge_html if served["n"] == 1 else "<html>done</html>"
                    route.fulfill(status=200, headers={
                        "Content-Type": "text/html; charset=utf-8"}, body=body)

                page.route("**/*", fulfill)
                page.goto(f"{config.SSO_BASE}/register",
                          wait_until="domcontentloaded", timeout=30000)
                # 挑战 JS 算完 cookie 后用 setTimeout(…, 2ms) 触发 reload。
                # 轮询 cookie，算出即回 —— 比固定等 4s 快一个量级。
                deadline = time.time() + 12
                while time.time() < deadline:
                    time.sleep(0.2)
                    for c in ctx.cookies():
                        if c["name"] == "acw_sc__v2":
                            return c["value"]
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    except Exception:
        # 共享浏览器可能已损坏：复位，让下一次调用重建
        try:
            _reset_waf_shared()
        except Exception:
            pass
        return None
    return None


# 精确路径被 405 硬拦时，用来诱导挑战的"变体路径"。
# 实测：WAF 自定义规则精确命中 `/register/byEmail`（含 `?query` 与尾 `/`
# 变体）；大小写 / api/v2 之类会落到默认挑战通道，能被 cookie 穿越。
WAF_VARIANT_PATHS = (
    "/Register/byEmail",
    "/api/v2/register/byEmail",
)


def _elicit_waf_cookie(client, payload: dict) -> str | None:
    """精确路径 405 时，用变体路径诱导同一会话再战，返回 acw_sc__v2。

    探针 body 刻意换成一封一次性垃圾邮箱 + 明文字符串：
    无论 WAF 放不放行，后端都不会真的注册成功（A0216 密码解密失败 /
    A0232 域名不支持），我们只要挑战页算出的 cookie。
    """
    h = client._headers()
    probe = dict(payload)
    probe["email"] = f"wafprobe{random.randint(100000, 999999)}@waf.invalid"
    probe["password"] = "wafprobe"
    for path in WAF_VARIANT_PATHS:
        rv = client.session.post(f"{client.gw}{path}", headers=h, json=probe,
                                 timeout=client.timeout)
        if rv.status_code == 200 and "aliyunwaf" in rv.text:
            c = _compute_waf_cookie(rv.text)
            if c:
                return c
    return None


@dataclass
class RegisterResult:
    ok: bool
    sso_uid: str = ""
    email: str = ""
    username: str = ""
    msg_code: str = ""
    msg: str = ""


class SSOClient:
    def __init__(self, timeout: int = None, proxy: str = None):
        self.gw = config.SSO_GW
        self.timeout = timeout or config.REQUEST_TIMEOUT
        self.session = requests.Session()
        # 出口代理。目标站点的封禁是 IP 维度，换 IP 靠这里。
        # `proxy` 传具体值时只作用于这个 client（槽位池并发场景必须这样用 ——
        # 改全局 `config.IR_PROXY` 在多个 producer 之间会互相踩）；
        # 传 None 时退回全局 `IR_PROXY`。
        # 注意 `apply_proxy` 会同时关掉 `trust_env` —— 否则环境里的
        # `HTTP_PROXY`（本机是 Clash）会把我们指定的代理**静默盖掉**。
        config.apply_proxy(self.session, proxy)
        self.proxy = proxy
        # 阿里云 WAF 的 acw_sc__v2 cookie：本实例现算、只在本 session 生效。
        # `_waf_set` 表示"已注入过 cookie"——若再次被拦说明 cookie 已过期，须重算。
        self._waf_cookie: str | None = None
        self._waf_set = False
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "lang": "zh-CN",
            "Origin": config.SSO_BASE,
            "User-Agent": config.USER_AGENT,
        })

    def _headers(self, referer_path: str = "/register") -> dict:
        h = dict(self.session.headers)
        h["Referer"] = f"{config.SSO_BASE}{referer_path}"
        return h

    def _post(self, path: str, payload: dict, *, referer: str = "/register",
              attempts: int = 4) -> requests.Response:
        """带退避重试的 POST。

        🔴 为什么必须有：`register/byEmail` 有写操作限流。实测 4 路并发注册时
        3 路立刻拿到 `429 Too Many Requests`（~1.2s 就返回，不是超时）。
        把 429 当注册失败会让批量任务大面积假失败 —— 它只是"慢点再来"。

        ⚠ 2026-09-20 删掉了原先的 `auth: str = None` 形参（连同一个
        `if auth: h["Authorization"] = …` 分支）：它唯一的调用者是已删除的
        `internal_auth()`，删后全仓无调用者传 `auth=`（已 grep 确认）。
        """
        h = self._headers(referer)
        url = f"{self.gw}{path}"
        last = None
        # WAF 相关的重试不算进 attempts 预算：拿 cookie / 变体诱导都是
        # "为了发出真正请求的准备工作"，不能让它们消耗业务重试次数。
        real_attempts = 0
        i = 0
        while True:
            r = self.session.post(url, headers=h, json=payload, timeout=self.timeout)
            # 阿里云 WAF 挑战页拦截：200 + text/html + aliyunwaf 特征，
            # 无 JS 能力的 requests 会被卡在这。取 cookie 注入后重试。
            if (r.status_code == 200
                    and (r.headers.get("content-type") or "").startswith("text/html")
                    and "aliyunwaf" in r.text):
                # 已注入过仍被拦 ⇒ cookie 过期，拿**当前**挑战页重算
                if self._waf_cookie is None or self._waf_set:
                    self._waf_cookie = _compute_waf_cookie(r.text)
                    self._waf_set = False
                if self._waf_cookie:
                    self._waf_set = True
                    self.session.cookies.set(
                        "acw_sc__v2", self._waf_cookie,
                        domain=sso_host_for_cookie(self.gw), path="/",
                    )
                    continue
                return r
            # 阿里云 WAF 硬拦：405 + data-spm 拦截页（无挑战特征）。
            # 🔴 不在这一层反复重试：405 是 **IP 维度**硬封，每一次写请求
            #   都会重置冷却窗口（实测：连刷 elicitation 会让封锁无限续命）。
            #   最多做**一次**变体诱导 —— 只有当"精确路径被自定义规则拦、
            #   变体路径还在默认挑战通道"时才能用 cookie 就地穿越；
            #   若诱导后重试仍 405，说明整 IP 都被封，立刻交给外层冷却
            #   （驱动层 / 批次节奏负责静默等待，这里不做长睡）。
            if r.status_code == 405:
                if not (self._waf_cookie and self._waf_set):
                    c = _elicit_waf_cookie(self, payload)
                    if c:
                        self._waf_cookie = c
                        self._waf_set = True
                        self.session.cookies.set(
                            "acw_sc__v2", c,
                            domain=sso_host_for_cookie(self.gw), path="/",
                        )
                        # 带 cookie 立刻重试一次；若仍 405 就交给下面的 break
                        r = self.session.post(url, headers=h, json=payload,
                                              timeout=self.timeout)
                        if (r.status_code == 200
                                and (r.headers.get("content-type") or "").startswith("text/html")
                                and "aliyunwaf" in r.text):
                            continue    # 又变成挑战页：交给上面挑战分支处理
                        if r.status_code != 405:
                            return r    # 穿越成功 / 或其它状态，直接返回
                last = r
                break
            if r.status_code == 429 or r.status_code >= 500:
                last = r
                real_attempts += 1
                if real_attempts > attempts:
                    break
                # 优先用服务端给的 Retry-After，没有就指数退避
                ra = r.headers.get("Retry-After")
                try:
                    delay = float(ra) if ra else 1.5 * (2 ** i)
                except (TypeError, ValueError):
                    delay = 1.5 * (2 ** i)
                delay = min(delay, 20.0) + random.uniform(0, 1.2)
                time.sleep(delay)
                i += 1
                continue
            return r
        last.raise_for_status()
        return last

    # ── 可用性校验 ────────────────────────────────────────────
    def check_username(self, username: str) -> bool:
        """True 表示用户名可用。"""
        r = self._post("/personal/username/check", {"username": username})
        return r.json().get("data") is True

    # ── 注册 ──────────────────────────────────────────────────
    def register(self, username: str, email: str, password: str) -> RegisterResult:
        payload = {
            "username": username,
            "email": email,
            "password": encrypt_password(email, password),
            "source": config.SOURCE,
            "clientId": config.CLIENT_ID,
        }
        r = self._post("/register/byEmail", payload)
        body = r.json()
        data = body.get("data") or {}
        return RegisterResult(
            ok=body.get("success") is True,
            sso_uid=str(data.get("ssoUid", "")),
            email=data.get("email", ""),
            username=data.get("username", ""),
            msg_code=body.get("msgCode", ""),
            msg=body.get("msg", ""),
        )

    # ── 激活 ──────────────────────────────────────────────────
    def activate(self, token: str, sign: str) -> bool:
        r = self._post("/register/active", {"token": token, "sign": sign},
                       referer="/active")
        return r.json().get("success") is True

    def activate_from_url(self, url: str) -> bool:
        """从激活链接中解析 token/sign 并激活。"""
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(url).query)
        token = (qs.get("token") or [""])[0]
        sign = (qs.get("sign") or [""])[0]
        if not token or not sign:
            raise ValueError(f"activation url missing token/sign: {url}")
        return self.activate(token, sign)
