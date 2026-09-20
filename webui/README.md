# webui/ —— Web 控制台

缝合自 [kaoqy/intern-register-web](https://github.com/kaoqy/intern-register-web)（FastAPI + 原生 HTML/JS，MIT 无声明版权），
作为本仓库的**可视化操作控制台**：浏览器里即可完成 临时邮箱 → SSO 注册 → 激活 → 领额度 → 建 Key 的每一步手工操作。

## 与原版的差异

| 项 | 原版 | 本仓库缝合版 |
|---|---|---|
| 配置存储 | 容器内 `/app/data/config.json` | 本机 `~/.intern-register-tool/webui-data/config.json`（`APP_DATA_DIR` 可覆盖） |
| 邮箱提供者 | 仅 CF Worker | worker + **yyds**（复用 `src.yyds_client`） |
| 代理解析 | `proxies()` 有掩码 bug（`{pwd}` 写成 `***}`） | 已修复 |
| 运行方式 | Docker | `python web.py` 或**打包成单文件 exe 双击启动** |

## 启动

```bash
python web.py                 # 启动并自动打开浏览器（默认 http://127.0.0.1:8000）
python web.py --port 9000     # 指定端口（被占用自动顺延）
python web.py --no-browser    # 不自动开浏览器
```

## 打包成单文件可执行

```bash
# Windows：
build.cmd                     # 一条命令完成：装依赖 → 装 PyInstaller → 打包
# 产物：dist\InternRegisterWeb.exe，双击即启动，自动开浏览器

# Linux / macOS 同理：
python -m pip install pyinstaller
python -m PyInstaller packaging/web.spec --noconfirm --distpath dist
```

## 使用流程

1. 「⚙️ 应用设置」→ 选提供者（CF Worker / YYDS Mail）→ 填对应凭据 → 保存
2. 「🔍 连接检测」验证 邮箱通道 / SSO / Discovery 连通性
3. 「1️⃣ 创建临时邮箱」→ 「2️⃣ SSO 注册」→ 「3️⃣ 等待邮件激活」（自动提取激活链接并调用激活）
4. 「4️⃣ 登录获取 JWT」⚠ 需人工处理阿里云人机验证（登录发生在外部浏览器，与打包无关）
5. 「5️⃣ 领取额度 & 创建 API Key」

> 与命令行链路的关系：本控制台是**手工分步**操作台（登录要人工过验证码）；
> 全自动批量（含无头浏览器自动登录）仍用 `python run.py --count N`。
