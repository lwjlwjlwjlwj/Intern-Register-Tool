"""Web UI 包 —— 缝合自 kaoqy/intern-register-web（FastAPI + 原生前端）。

本包把该作者的 Web 控制台接入本仓库，并做了三处适配：
  1. 配置持久化位置改为本机用户目录（默认 `~/.intern-register-tool/webui-data`），
     不依赖 Docker 的 /app/data，打包成可执行文件后也能直接读写；
  2. 邮箱提供者支持 worker（CF Worker，原版默认）与 yyds（本仓库的 YYDS Mail），
     通过 webui/clients.py 的 create_mail_client() 工厂分发；
  3. 修复原版 proxies() 里的掩码 bug（`{pwd}` 被误写成了 `***}`）。
"""
