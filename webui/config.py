"""Web UI 全局配置 —— 应用内管理，持久化到 JSON 文件（不依赖 .env）。"""

import json
import os
import threading
from pathlib import Path

# ── 配置文件路径 ────────────────────────────────────────────────
# 打包成单文件 exe 后没有"仓库根"，所以默认落到用户目录，避免写到
# 只读的安装位置；部署时可用 APP_DATA_DIR 环境变量覆盖。
DATA_DIR = Path(os.getenv("APP_DATA_DIR", "") or Path.home() / ".intern-register-tool" / "webui-data")
CONFIG_FILE = DATA_DIR / "config.json"

# ── 默认配置 ────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    # 邮箱提供者：worker（CF Worker，原版）| yyds（YYDS Mail，本仓库支持）
    "provider": "worker",
    "worker_base": "",
    "worker_admin_token": "",
    "worker_domain": "",
    "yyds_api_key": "",
    "yyds_base_url": "https://maliapi.215.im/v1",
    "yyds_domain": "",
    "yyds_subdomain": "",
    "proxy": "",
    "client_id": "dagw07mkg1bazlxzoy31",
    "source": "discovery",
    "mail_poll_interval": 0.8,
    "mail_poll_timeout": 120,
    "request_timeout": 30,
    "reg_quota_max": 40,
    "reg_quota_window_h": 24,
}

# ── 运行时配置（线程安全）──────────────────────────────────────
_CONFIG: dict = {}
_LOCK = threading.Lock()


def _load_config() -> dict:
    """从 JSON 文件加载配置。"""
    if not CONFIG_FILE.is_file():
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_CONFIG)
        merged.update(data)
        return merged
    except (ValueError, OSError):
        return dict(DEFAULT_CONFIG)


def _save_config(cfg: dict) -> None:
    """持久化配置到 JSON 文件。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_config() -> dict:
    """获取当前配置（线程安全）。"""
    with _LOCK:
        if not _CONFIG:
            _CONFIG.update(_load_config())
        return dict(_CONFIG)


def get_config_value(key: str, default=None):
    """获取单个配置项。"""
    return get_config().get(key, default)


def update_config(updates: dict) -> dict:
    """更新配置并持久化。"""
    with _LOCK:
        _CONFIG.update(updates)
        _save_config(_CONFIG)
        return dict(_CONFIG)


def reset_config() -> dict:
    """重置为默认配置。"""
    with _LOCK:
        _CONFIG.clear()
        _CONFIG.update(DEFAULT_CONFIG)
        _save_config(_CONFIG)
        return dict(_CONFIG)


def validate() -> list[str]:
    """检查必填配置是否完整（按 provider 分支）。"""
    cfg = get_config()
    missing = []
    if cfg.get("provider") == "yyds":
        if not cfg.get("yyds_api_key"):
            missing.append("yyds_api_key")
    else:
        if not cfg.get("worker_base"):
            missing.append("worker_base")
        if not cfg.get("worker_admin_token"):
            missing.append("worker_admin_token")
        if not cfg.get("worker_domain"):
            missing.append("worker_domain")
    return missing


# ── 兼容旧接口（原版用模块级 @property，那会得到 property 对象而不是值，
#    FastAPI 序列化 / 客户端拼接时都会炸 —— 这里改成普通函数，调用处加括号）──
def MAIL_PROVIDER():
    return get_config_value("provider", "worker")


def WORKER_BASE():
    return get_config_value("worker_base", "")


def WORKER_ADMIN_TOKEN():
    return get_config_value("worker_admin_token", "")


def WORKER_DOMAIN():
    return get_config_value("worker_domain", "")


def YYDS_API_KEY():
    return get_config_value("yyds_api_key", "")


def YYDS_BASE_URL():
    return get_config_value("yyds_base_url", "https://maliapi.215.im/v1")


def YYDS_DOMAIN():
    return get_config_value("yyds_domain", "")


def YYDS_SUBDOMAIN():
    return get_config_value("yyds_subdomain", "")


def IR_PROXY():
    return get_config_value("proxy", "")


def CLIENT_ID():
    return get_config_value("client_id", "dagw07mkg1bazlxzoy31")


def SOURCE():
    return get_config_value("source", "discovery")


def MAIL_POLL_INTERVAL():
    return get_config_value("mail_poll_interval", 0.8)


def MAIL_POLL_TIMEOUT():
    return get_config_value("mail_poll_timeout", 120)


def REQUEST_TIMEOUT():
    return get_config_value("request_timeout", 30)


def REG_QUOTA_MAX():
    return get_config_value("reg_quota_max", 40)


def REG_QUOTA_WINDOW_H():
    return get_config_value("reg_quota_window_h", 24)


SSO_BASE = "https://sso.openxlab.org.cn"
SSO_GW = f"{SSO_BASE}/gw/uaa-be/api/v1"
DISCOVERY_BASE = "https://discovery.intern-ai.org.cn"
DISCOVERY_API = f"{DISCOVERY_BASE}/api"
CHAT_API_BASE = "https://discovery-api.intern-ai.org.cn/v1"

SSO_PUBKEY_B64 = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCOst3X5k3uqRpKtFOfLQdh5ZyakdP0fnP6CyPs"
    "9e2BWF/Jud+BZNNWOPtm5roUu3Cf0wFbvha4uD+XxmNz/3Ea+VOrfbhIeSWX3CTZ+9oAWERz0ftF"
    "oEYTf2nAt5LORhhNHt2Wea8yMTD8GoZ/asm2GX3B/CjIa6PwVlbRHX9/bwIDAQAB"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)


def proxies(raw: str = None) -> dict | None:
    """把代理串解析成 requests 的 proxies 字典。"""
    raw = (get_config_value("proxy") if raw is None else raw).strip()
    if not raw:
        return None
    if "://" not in raw:
        parts = raw.split(":")
        if len(parts) == 4:
            host, port, user, pwd = parts
            raw = f"http://{user}:{pwd}@{host}:{port}"
        elif len(parts) == 2:
            raw = f"http://{raw}"
        else:
            raise ValueError(f"代理串格式无法识别：{raw!r}")
    return {"http": raw, "https": raw}


def apply_proxy(session, proxy: str = None) -> None:
    """把代理挂到 requests.Session 上。"""
    px = proxies(proxy)
    if not px:
        return
    session.proxies = px
    session.trust_env = False
