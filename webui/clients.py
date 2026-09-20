"""temp-mail / SSO / Discovery 客户端封装。

原版（kaoqy/intern-register-web）只支持 CF Worker 临时邮箱；
本仓库给它加了 create_mail_client() 工厂：按配置里的 provider 分发
  - worker：原版 TempMailClient
  - yyds：本仓库 src.yyds_client.YydsMailClient 的 Web 适配（仅实现
    create_mailbox / wait_for_mail / wait_for_activation_link，
    邮件列表类接口在 yyds 模式下不支持，会给出明确报错）。
"""

import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import requests

from . import config


@dataclass
class Mail:
    id: str
    to_address: str
    from_address: str
    subject: str
    body: str
    raw: str = ""
    extracted_json: str = "[]"
    received_at: int = 0

    @property
    def links(self) -> list[str]:
        try:
            items = json.loads(self.extracted_json or "[]")
        except (ValueError, TypeError):
            return []
        return [str(it["value"]) for it in items if isinstance(it, dict) and it.get("value")]

    def find_link(self, *keywords: str) -> str | None:
        kws = [k.lower() for k in keywords] or ["active", "activat"]
        for url in self.links:
            low = url.lower()
            if any(k in low for k in kws):
                return url
        return None


class TempMailClient:
    """CF Worker 临时邮箱客户端（原版）。"""

    def __init__(self, base: str = None, token: str = None, timeout: int = 30):
        self.base = (base or config.WORKER_BASE()).rstrip("/")
        self.token = token or config.WORKER_ADMIN_TOKEN()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "X-Admin-Token": self.token,
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self.last_error = ""
        self.last_polls = 0

    def create_mailbox(self, domain: str = None, count: int = 1) -> list[str]:
        payload = {"count": count}
        if domain:
            payload["domain"] = domain
        r = self.session.post(f"{self.base}/api/mailboxes", json=payload, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"create_mailbox failed: {data}")
        return data.get("emails", [])

    def list_mails(self, email: str = None, limit: int = 20) -> list[Mail]:
        if email:
            r = self.session.get(f"{self.base}/api/inbox", params={"email": email}, timeout=self.timeout)
        else:
            r = self.session.get(f"{self.base}/admin/mails", params={"limit": limit}, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        raw = data.get("messages") or data.get("mails") or []
        return [self._parse_mail(m) for m in raw]

    def get_mail(self, mail_id: str) -> Mail | None:
        r = self.session.get(f"{self.base}/admin/mails/{mail_id}", timeout=self.timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return self._parse_mail(r.json())

    def wait_for_mail(
        self,
        address: str,
        sender_contains: str = None,
        timeout: int = 60,
        interval: float = 1.0,
    ) -> tuple[Mail | None, str, int]:
        target = address.lower()
        deadline = time.time() + timeout
        polls = 0
        errors = 0

        while time.time() < deadline:
            polls += 1
            try:
                mails = self.list_mails(email=address)
            except requests.HTTPError as ex:
                code = ex.response.status_code if ex.response else 0
                if code and code < 500:
                    return None, f"HTTP {code}: {ex}", polls
                errors += 1
                time.sleep(min(interval * (1 + errors // 5), 2.0))
                continue

            for m in mails:
                if m.to_address.lower() != target:
                    continue
                if sender_contains and sender_contains.lower() not in m.from_address.lower():
                    continue
                return m, "", polls

            time.sleep(interval)

        return None, f"No mail received within {timeout}s", polls

    def wait_for_activation_link(
        self,
        address: str,
        sender_contains: str = None,
        timeout: int = 60,
        interval: float = 1.0,
    ) -> tuple[str | None, str, int]:
        mail, err, polls = self.wait_for_mail(address, sender_contains, timeout, interval)
        if not mail:
            return None, err, polls
        link = mail.find_link("active", "activat", "verif", "confirm")
        if link:
            return link, "", polls
        return None, "Mail received but no activation link found", polls

    def _parse_mail(self, data: dict) -> Mail:
        return Mail(
            id=str(data.get("id", "")),
            to_address=str(data.get("to_address", "")),
            from_address=str(data.get("from_address", "")),
            subject=str(data.get("subject", "")),
            body=str(data.get("body") or data.get("body_text") or ""),
            raw=str(data.get("raw") or ""),
            extracted_json=str(data.get("extracted_json") or "[]"),
            received_at=int(data.get("received_at") or 0),
        )


class YydsWebAdapter:
    """YYDS Mail 的 Web 适配器（包住本仓库的 src.yyds_client.YydsMailClient）。

    只实现 Web 界面用到的操作：建邮箱、等待邮件、等待激活链接。
    邮件列表 / 单封读取在 yyds 模式（按账号一对一邮箱）下不适用，
    调用会抛出明确的 NotImplementedError，由接口层转成 501 返回。
    """

    def __init__(self):
        from src.yyds_client import YydsMailClient

        self._client = YydsMailClient(
            api_key=config.YYDS_API_KEY(),
            base_url=config.YYDS_BASE_URL(),
            domain=config.YYDS_DOMAIN(),
            subdomain=config.YYDS_SUBDOMAIN(),
        )

    def create_mailbox(self, domain: str = None, count: int = 1) -> list[str]:
        return self._client.create_mailbox(domain=domain, count=count)

    def wait_for_mail(self, address: str, sender_contains: str = None,
                      timeout: int = 60, interval: float = 1.0):
        mail = self._client.wait_for_mail(
            address, sender_contains=sender_contains or "openxlab",
            timeout=timeout, interval=interval)
        if mail is None:
            return None, f"No mail received within {timeout}s", 0
        return mail, "", 0

    def wait_for_activation_link(self, address: str, sender_contains: str = None,
                                 timeout: int = 60, interval: float = 1.0):
        mail = self._client.wait_for_mail(
            address, sender_contains=sender_contains or "openxlab",
            timeout=timeout, interval=interval)
        if mail is None:
            return None, f"No mail received within {timeout}s", 0
        link = mail.find_link("active", "activat", "verif", "confirm")
        if link:
            return link, "", 0
        return None, "Mail received but no activation link found", 0

    def list_mails(self, email: str = None, limit: int = 20):
        raise NotImplementedError("yyds 模式按「一账号一邮箱」设计，不支持邮件列表查询")

    def get_mail(self, mail_id: str):
        raise NotImplementedError("yyds 模式按「一账号一邮箱」设计，不支持按 id 读单封邮件")


def create_mail_client():
    """按配置的 provider 返回邮箱客户端。"""
    if config.MAIL_PROVIDER() == "yyds":
        return YydsWebAdapter()
    return TempMailClient()


class SSOClient:
    def __init__(self, proxy: str = None):
        self.gw = config.SSO_GW
        self.timeout = config.REQUEST_TIMEOUT()
        self.session = requests.Session()
        config.apply_proxy(self.session, proxy)
        self.proxy = proxy
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

    def _post(self, path: str, payload: dict, *, referer: str = "/register") -> requests.Response:
        h = self._headers(referer)
        url = f"{self.gw}{path}"
        r = self.session.post(url, headers=h, json=payload, timeout=self.timeout)
        return r

    def check_username(self, username: str) -> bool:
        r = self._post("/personal/username/check", {"username": username})
        return r.json().get("data") is True

    def register(self, username: str, email: str, password: str) -> dict:
        payload = {
            "username": username,
            "email": email,
            "password": password,
            "source": config.SOURCE(),
            "clientId": config.CLIENT_ID(),
        }
        r = self._post("/register/byEmail", payload)
        body = r.json()
        return {
            "ok": body.get("success") is True,
            "sso_uid": str((body.get("data") or {}).get("ssoUid", "")),
            "msg_code": body.get("msgCode", ""),
            "msg": body.get("msg", ""),
        }

    def activate(self, token: str, sign: str) -> bool:
        r = self._post("/register/active", {"token": token, "sign": sign}, referer="/active")
        return r.json().get("success") is True

    def activate_from_url(self, url: str) -> bool:
        qs = parse_qs(urlparse(url).query)
        token = (qs.get("token") or [""])[0]
        sign = (qs.get("sign") or [""])[0]
        if not token or not sign:
            raise ValueError(f"activation url missing token/sign: {url}")
        return self.activate(token, sign)


class DiscoveryClient:
    def __init__(self, jwt: str = "", timeout: int = 30):
        self.base = config.DISCOVERY_API
        self.jwt = jwt
        self.timeout = timeout
        self.session = requests.Session()
        config.apply_proxy(self.session)
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": config.DISCOVERY_BASE,
            "Referer": f"{config.DISCOVERY_BASE}/token-plan/home?tabIndex=0",
            "User-Agent": config.USER_AGENT,
        })
        if jwt:
            self.session.headers["Authorization"] = f"Bearer {jwt}"

    def free_grant_status(self) -> dict:
        r = self.session.get(f"{self.base}/tokenplan/v1/users/free-grant-status", timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("data") or {}

    def claim_free_grant(self) -> dict:
        r = self.session.post(f"{self.base}/tokenplan/v1/users/free-grant", timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("data") or {}

    def balance(self) -> dict:
        r = self.session.get(f"{self.base}/tokenplan/v1/credits/balance", timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("data") or {}

    def create_key(self, name: str = "default") -> dict:
        import uuid
        r = self.session.post(
            f"{self.base}/tokenplan/v1/keys",
            json={"name": name},
            headers={"Idempotency-Key": str(uuid.uuid4())},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json().get("data") or {}

    def list_keys(self) -> list[dict]:
        r = self.session.get(f"{self.base}/tokenplan/v1/keys", timeout=self.timeout)
        r.raise_for_status()
        body = r.json()
        if body.get("code") not in (0, None):
            raise RuntimeError(f"list_keys failed: code={body.get('code')} msg={body.get('msg')}")
        return (body.get("data") or {}).get("items", []) or []