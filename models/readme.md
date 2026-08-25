# models — YOLO 权重目录

> 将训练产出的 `.pt` 放到对应子目录；路径在 `config/default.yaml` / `vision` 与 HMI 视觉页中选择。

---

## 子目录约定

| 目录 | 用途 |
|------|------|
| `shoe_vision/` | 皮带鞋 OBB、鞋头分类等 |
| `slot_check/` | 槽位有无检测 |
| `toe_align/` | 鞋头对位 |
| `position/slot/`、`position/rod/` | cam4 槽/压杆（position 管线） |
| `legacy/` | 从旧 Casbot_Press_Shoes 拷贝的权重 |

---

## 获取权重

1. HMI **视觉 → 采图训练** 训练后导出  
2. 从旧工程 `models/` 复制（见 [tools/yolo_train/README.md](../tools/yolo_train/README.md)）  
3. 算法挂接说明：[algorithm_module/readme.md](../algorithm_module/readme.md) 模型表

---

## 注意

- 大体积 `.pt` 建议 `.gitignore`，现场单独拷贝  
- 换模型后重启程序或 HMI 内刷新模型列表
