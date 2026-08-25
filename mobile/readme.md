# mobile — 简易 Web 监控（可选）

> 轻量 HTTP 服务，浏览器查看状态；**非**主 HMI，主界面仍为 PySide6。

---

## 文件

| 文件 | 作用 |
|------|------|
| `web_server.py` | 启动内置 Web 服务 |
| `static/index.html` | 简单监控页 |

是否在 `main.py` / 协调器中启用，见 `config/default.yaml` 相关开关（若有）。

---

## 使用

按 [docs/操作说明.md](../docs/操作说明.md) 或代码内注释启动；默认端口以配置为准。
