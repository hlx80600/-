# config — 现场配置

> **改 IP、相机 serial、示教点、Mock 后请重启 `main.py`。**

---

## 核心文件

| 文件 / 目录 | 作用 |
|-------------|------|
| **`default.yaml`** | **唯一参数总表**：机器人、夹爪、压机、相机、运动步、vision |
| `roi/camN.json` | 各相机 ROI（HMI 视觉页写入） |
| `calib/` | 棋盘格内参、手眼采样与矩阵备份 |

根目录另有：

| 文件 | 作用 |
|------|------|
| `shoe_vision_config.json` | cam1 皮带 YOLO + 内参 + 手眼（生产） |
| `position_config.yaml` | cam4 压杆/槽位（若启用 position 管线） |

---

## 修改方式

1. **直接编辑** `default.yaml`（Git 跟踪，适合开发）  
2. **HMI** → 设置 → 通信与设备 / 相关页保存（写回 yaml）

字段说明与检查清单：[docs/操作说明.md](../docs/操作说明.md)。

---

## 注意

- 勿把现场私密 IP、密码提交到公开仓库  
- 删除的标定文件可从 `calib/` 备份或 HMI 重新标定恢复
