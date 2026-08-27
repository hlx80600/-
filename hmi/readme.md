# hmi — PySide6 人机界面

> 触摸屏主窗口、各功能页、多语言、在线「使用说明」。

---

## 主要文件

| 文件 | 作用 |
|------|------|
| `main_window.py` | 主窗口、侧栏导航、页切换 |
| `tab_titles.py` | 页签标题与路由 |
| `style.py` | 主题与控件样式 |
| `scroll_util.py` | 长页滚动容器 |
| `help_content.py` / `help_content_en.py` | 在线手册正文（中/英） |
| `alarm_dialog.py` | 报警弹窗 |
| `i18n/` | 多语言：`locales/*.py`、`fonts.py` |
| `pages/` | 各功能页（监控、视觉、设置、调试等） |

---

## 常用页面目录

| 路径 | 页面 |
|------|------|
| `pages/monitor_page.py` | 运行监控 |
| `pages/vision_hub_page.py` / `vision_workspace.py` | 视觉中心 |
| `pages/points_page.py` | 示教点位 |
| `pages/jog_pendant.py` | 独立示教器窗口（点动封装） |
| `pages/step_debug_page.py` | 运动步调试 |
| `pages/config_page.py` | 通信与设备 |
| `pages/settings_*` | 设置（语言、界面） |
| `pages/help_page.py` | 使用说明 |

---

## 多语言

语言在 `config/default.yaml` → `system.hmi.language`，或在 **设置 → 语言** 切换。  
新增文案：在 `hmi/i18n/locales/zh_cn.py` 与对应语言文件添加 key，页内调用 `i18n.tr(...)`。

---

## 相关文档

- 现场怎么点：[docs/界面操作手册.md](../docs/界面操作手册.md)  
- 与代码同步的页说明：运行程序 → **Help / 使用说明**
