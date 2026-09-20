"""FastAPI Web 主应用 —— 应用内配置管理（缝合自 kaoqy/intern-register-web）。

改动点（相对原版）：
  - 模板/静态资源路径在 PyInstaller 打包后走 sys._MEIPASS；
  - 邮箱客户端走 webui.clients.create_mail_client() 工厂（支持 yyds）；
  - /api/check 按 provider 分支检测；yyds 模式下邮件列表类接口返回 501。
"""

from __future__ import annotations

import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

if getattr(sys, "frozen", False):  # PyInstaller 单文件模式
    _BASE = Path(sys._MEIPASS) / "webui"
else:
    _BASE = Path(__file__).resolve().parent

from . import config  # noqa: E402
from .clients import DiscoveryClient, SSOClient, create_mail_client  # noqa: E402

TASKS: dict[str, dict] = {}
TASKS_LOCK = threading.Lock()


def get_client():
    """按配置返回邮箱客户端；配置缺失时给出明确 500。"""
    missing = config.validate()
    if missing:
        raise HTTPException(status_code=500, detail=f"Missing config: {', '.join(missing)}")
    return create_mail_client()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Intern Register Web", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(_BASE / "templates"))


# ── 前端页面 ────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    cfg = config.get_config()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "config": {
                "provider": cfg.get("provider", "worker"),
                "worker_base": cfg.get("worker_base", ""),
                "worker_domain": cfg.get("worker_domain", ""),
                "worker_admin_token": cfg.get("worker_admin_token", ""),
                "yyds_api_key": cfg.get("yyds_api_key", ""),
                "yyds_base_url": cfg.get("yyds_base_url", ""),
                "yyds_domain": cfg.get("yyds_domain", ""),
                "yyds_subdomain": cfg.get("yyds_subdomain", ""),
                "proxy": cfg.get("proxy", ""),
                "client_id": cfg.get("client_id", ""),
                "source": cfg.get("source", ""),
            }
        },
    )


# ── 健康检查 ────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    missing = config.validate()
    return {
        "ok": not missing,
        "missing": missing,
        "provider": config.get_config_value("provider", "worker"),
        "config": {
            "worker_base": config.get_config_value("worker_base", ""),
            "worker_domain": config.get_config_value("worker_domain", ""),
        }
    }


# ── 配置管理 ────────────────────────────────────────────────────
@app.get("/api/config")
async def get_config():
    """获取当前配置（脱敏）。"""
    cfg = config.get_config()
    return {
        "provider": cfg.get("provider", "worker"),
        "worker_base": cfg.get("worker_base", ""),
        "worker_domain": cfg.get("worker_domain", ""),
        "worker_admin_token": "***" if cfg.get("worker_admin_token") else "",
        "yyds_api_key": "***" if cfg.get("yyds_api_key") else "",
        "yyds_base_url": cfg.get("yyds_base_url", ""),
        "yyds_domain": cfg.get("yyds_domain", ""),
        "yyds_subdomain": cfg.get("yyds_subdomain", ""),
        "proxy": cfg.get("proxy", ""),
        "client_id": cfg.get("client_id", ""),
        "source": cfg.get("source", ""),
        "mail_poll_interval": cfg.get("mail_poll_interval", 0.8),
        "mail_poll_timeout": cfg.get("mail_poll_timeout", 120),
        "request_timeout": cfg.get("request_timeout", 30),
        "reg_quota_max": cfg.get("reg_quota_max", 40),
        "reg_quota_window_h": cfg.get("reg_quota_window_h", 24),
    }


@app.post("/api/config")
async def save_config(request: Request):
    """保存配置。"""
    data = await request.json()
    updates = {}
    for key in [
        "provider", "worker_base", "worker_domain", "worker_admin_token",
        "yyds_api_key", "yyds_base_url", "yyds_domain", "yyds_subdomain",
        "proxy", "client_id", "source", "mail_poll_interval",
        "mail_poll_timeout", "request_timeout", "reg_quota_max",
        "reg_quota_window_h",
    ]:
        if key in data:
            updates[key] = data[key]
    cfg = config.update_config(updates)
    return {"ok": True, "config": {k: cfg[k] for k in updates}}


@app.post("/api/config/reset")
async def reset_config():
    """重置配置。"""
    config.reset_config()
    return {"ok": True}


# ── 连接检测 ────────────────────────────────────────────────────
def _check_provider():
    """按 provider 检测邮箱通道连通性。"""
    cfg = config.get_config()
    if cfg.get("provider") == "yyds":
        if not cfg.get("yyds_api_key"):
            return {"ok": False, "error": "配置缺失: yyds_api_key"}
        try:
            from src.yydsmail import YydsMailProvider

            p = YydsMailProvider(
                api_key=cfg["yyds_api_key"],
                base_url=cfg.get("yyds_base_url") or "https://maliapi.215.im/v1",
                domain=cfg.get("yyds_domain"),
                subdomain=cfg.get("yyds_subdomain"),
            )
            domains = p.list_domains()
            return {"ok": True, "status": 200, "domains": domains}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    base = cfg.get("worker_base", "")
    token = cfg.get("worker_admin_token", "")
    try:
        import requests as _req

        r = _req.get(
            f"{base}/admin/addresses",
            headers={"X-Admin-Token": token, "Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if r.status_code == 401:
            return {"ok": False, "error": "认证失败，请检查 Admin Token"}
        if r.status_code == 200:
            return {"ok": True, "status": r.status_code}
        return {"ok": False, "status": r.status_code, "error": r.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/check")
async def check_connection():
    """检测 邮箱通道 / SSO / Discovery 连接是否正常。"""
    missing = config.validate()
    if missing:
        return {"ok": False, "error": f"配置缺失: {', '.join(missing)}"}

    result = {"ok": False, "checks": {}}
    result["checks"]["temp_mail"] = _check_provider()

    try:
        import requests
        r = requests.get(f"{config.SSO_BASE}/gw/uaa-be/api/v1/personal/username/check", timeout=10)
        result["checks"]["sso"] = {"ok": True, "status": r.status_code}
    except Exception as e:
        result["checks"]["sso"] = {"ok": False, "error": str(e)}

    try:
        import requests
        r = requests.get(f"{config.DISCOVERY_API}/tokenplan/v1/users/free-grant-status", timeout=10)
        result["checks"]["discovery"] = {"ok": r.status_code != 401, "status": r.status_code}
    except Exception as e:
        result["checks"]["discovery"] = {"ok": False, "error": str(e)}

    result["ok"] = all(c.get("ok") for c in result["checks"].values())
    return result


# ── 创建邮箱 ────────────────────────────────────────────────────
@app.post("/api/mailbox")
async def create_mailbox(
    domain: str | None = None,
    prefix: str | None = None,
    count: int = 1,
):
    """创建邮箱地址。

    - 指定 prefix：创建 prefix@domain（仅 worker 模式支持）
    - 指定 domain：生成随机地址
    - 都不指定：使用默认域名
    """
    client = get_client()
    cfg = config.get_config()
    try:
        if prefix:
            if cfg.get("provider") == "yyds":
                raise HTTPException(status_code=400, detail="yyds 模式不支持指定前缀，请使用随机地址")
            if not domain:
                domain = cfg.get("worker_domain", "")
            if not domain:
                raise HTTPException(status_code=400, detail="请提供域名")
            address = f"{prefix}@{domain}"
            r = client.session.post(
                f"{client.base}/admin/address",
                json={"address": address},
                timeout=client.timeout,
            )
            if r.status_code == 200:
                data = r.json()
                emails = data.get("emails", [address]) if data.get("ok") else [address]
                return {"ok": True, "emails": emails}
            else:
                raise HTTPException(status_code=r.status_code, detail=r.text)
        else:
            emails = client.create_mailbox(domain=domain, count=count)
            return {"ok": True, "emails": emails}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── 获取邮件列表 ────────────────────────────────────────────────
@app.get("/api/mails")
async def list_mails(
    email: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    client = get_client()
    try:
        mails = client.list_mails(email=email, limit=limit)
        return [
            {
                "id": m.id,
                "to_address": m.to_address,
                "from_address": m.from_address,
                "subject": m.subject,
                "links": m.links,
                "received_at": m.received_at,
            }
            for m in mails
        ]
    except NotImplementedError as ex:
        raise HTTPException(status_code=501, detail=str(ex)) from ex
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── 获取单封邮件 ────────────────────────────────────────────────
@app.get("/api/mails/{mail_id}")
async def get_mail(mail_id: str):
    client = get_client()
    try:
        mail = client.get_mail(mail_id)
        if not mail:
            raise HTTPException(status_code=404, detail="Mail not found")
        return {
            "id": mail.id,
            "to_address": mail.to_address,
            "from_address": mail.from_address,
            "subject": mail.subject,
            "body": mail.body,
            "links": mail.links,
            "extracted_json": mail.extracted_json,
            "received_at": mail.received_at,
        }
    except NotImplementedError as ex:
        raise HTTPException(status_code=501, detail=str(ex)) from ex
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── 轮询等待邮件 ────────────────────────────────────────────────
@app.post("/api/wait_mail")
async def wait_mail(
    address: str,
    sender_contains: str | None = None,
    timeout: int = 60,
    interval: float = 1.0,
):
    client = get_client()
    try:
        mail, err, polls = client.wait_for_mail(address, sender_contains, timeout, interval)
        if not mail:
            return {"ok": False, "error": err, "polls": polls}
        return {
            "ok": True,
            "polls": polls,
            "mail": {
                "id": mail.id,
                "to_address": mail.to_address,
                "from_address": mail.from_address,
                "subject": mail.subject,
                "links": mail.links,
                "received_at": mail.received_at,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── 轮询等待激活链接 ────────────────────────────────────────────
@app.post("/api/wait_activation_link")
async def wait_activation_link(
    address: str,
    sender_contains: str | None = None,
    timeout: int = 60,
    interval: float = 1.0,
):
    client = get_client()
    try:
        link, err, polls = client.wait_for_activation_link(address, sender_contains, timeout, interval)
        return {"ok": bool(link), "link": link or "", "error": err, "polls": polls}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── SSO 注册 ────────────────────────────────────────────────────
@app.post("/api/sso/register")
async def sso_register(username: str, email: str, password: str):
    sso = SSOClient()
    try:
        result = sso.register(username, email, password)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── SSO 激活 ────────────────────────────────────────────────────
@app.post("/api/sso/activate")
async def sso_activate(url: str):
    sso = SSOClient()
    try:
        ok = sso.activate_from_url(url)
        return {"ok": ok}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── Discovery 操作 ──────────────────────────────────────────────
@app.post("/api/discovery/claim_grant")
async def claim_grant(jwt: str):
    disc = DiscoveryClient(jwt=jwt)
    try:
        result = disc.claim_free_grant()
        return {"ok": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/discovery/create_key")
async def create_key(jwt: str, name: str = "default"):
    disc = DiscoveryClient(jwt=jwt)
    try:
        result = disc.create_key(name=name)
        return {"ok": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/discovery/keys")
async def list_keys(jwt: str):
    disc = DiscoveryClient(jwt=jwt)
    try:
        keys = disc.list_keys()
        return {"ok": True, "keys": keys}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/discovery/balance")
async def get_balance(jwt: str):
    disc = DiscoveryClient(jwt=jwt)
    try:
        balance = disc.balance()
        return {"ok": True, "balance": balance}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e