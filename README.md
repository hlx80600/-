# 莆田鞋厂四槽机器控制程序

**英文代号：** `Casbot_FourSlot_Press_Shoes`  
**一句话：** 四槽压鞋机 + 双臂（法奥 FR5）自动上下料 — HMI 触摸屏 + 六工位流程 + 四路视觉（YOLO / 手眼）。

工控机上的 **Python 3.10+** 主控程序：上料臂从皮带取鞋 → 压鞋机四槽压合转台 → 下料臂出料；夹爪 CAN、压机 Modbus、Orbbec 相机、空跑/Mock 联调均可在同一套 HMI 完成。

---

## 文档体系：本 README 与各 readme 怎么分工

| 文档 | 路径 | 写什么（不要重复造轮子） |
|------|------|--------------------------|
| **本文件 `README.md`** | 仓库根 | 项目总览、**第一次部署**、国内镜像安装、目录地图、**该读哪份文档** |
| [docs/从零看懂本程序.md](docs/从零看懂本程序.md) | `docs/` | 新人**阅读顺序**（5 步指路，无重复正文） |
| [docs/程序总览.md](docs/程序总览.md) | `docs/` | **架构 + 主循环 + 一条鞋流程** + 核心对象表 |
| [docs/界面操作手册.md](docs/界面操作手册.md) | `docs/` | **HMI 每页怎么点**、投产顺序、手眼逐步点击 |
| [docs/操作说明.md](docs/操作说明.md) | `docs/` | **yaml 改址表**、设备 IP/CAN/Modbus、联调检查清单 |
| [docs/夹爪使用说明.md](docs/夹爪使用说明.md) | `docs/` | 达妙 DM-J4310-2EC：48V、CAN、接线、试夹、GRIP 报警 |
| [docs/Codesys对照说明.md](docs/Codesys对照说明.md) | `docs/` | 旧 PLC/Codesys 变量与现程序对照（迁移用） |
| HMI「使用说明」 | `hmi/help_content.py` | 与界面同步的**在线手册**（页职责 / 实现文件 / 引用） |
| [algorithm_module/readme.md](algorithm_module/readme.md) | 算法包 | **视觉算法 API**：`algo.*` 入参出参、谁调用谁 |
| [visualize_module/readme.md](visualize_module/readme.md) | 可视化 | **相机监控窗**：取流、缓存推演、`viz` 门面 |
| [shoe_vision_readme.md](shoe_vision_readme.md) | 根目录 | 旧版 `ShoeVision` 封装（Orbbec + YOLO + handeye json） |
| [tools/yolo_train/README.md](tools/yolo_train/README.md) | 工具 | 命令行训练备查；日常训练优先用 HMI「视觉·采图训练」 |
| [datasets/README.md](datasets/README.md) | 数据 | 数据集目录约定 |
| [docs/readme.md](docs/readme.md) | `docs/` | 纸质文档索引与阅读顺序 |
| [core/readme.md](core/readme.md) | 主控 | 协调器、GVL、报警、空跑 |
| [stations/readme.md](stations/readme.md) | 工位 | Station1～6 与步序目录 |
| [devices/readme.md](devices/readme.md) | 设备 | FR5、夹爪 CAN、压机 Modbus |
| [vision/readme.md](vision/readme.md) | 视觉业务 | VisionService、标定、手眼 |
| [hmi/readme.md](hmi/readme.md) | 界面 | PySide6 页签与 i18n |
| [config/readme.md](config/readme.md) | 配置 | `default.yaml` 与 roi/calib |
| [models/readme.md](models/readme.md) | 模型 | YOLO `.pt` 子目录约定 |
| [press_shoes/readme.md](press_shoes/readme.md) | 旧样例 | 旧 workflow；**新逻辑在 `devices/` + `stations/`** |
| [mobile/readme.md](mobile/readme.md) | Web | 可选简易浏览器监控 |

**原则：** 整机流程 → `docs/程序总览.md`；改 IP/地址 → `docs/操作说明.md` + `config/default.yaml`；视觉接口 → `algorithm_module/readme.md`；现场怎么点 → `docs/界面操作手册.md` 或 HMI「使用说明」；各目录细节 → 上表对应 `readme.md`。

---

## 第一次部署（新机 / 新工控机）

### 1. 硬件与系统

| 项 | 要求 |
|----|------|
| OS | Linux（Ubuntu 22.04+ 常见）；桌面需 X11 或 Wayland |
| Python | **3.10+**（3.12 已测） |
| 网络 | 与两台 FR5、压机、相机同一局域网；机器人 IP 在 `config/default.yaml` |
| CAN | 夹爪用 SocketCAN（如 `can0`），1 Mbps |
| 显卡 | 视觉训练可选 NVIDIA GPU；仅运行 HMI+CPU 推理可不要 GPU |

### 2. 克隆与目录

```bash
git clone https://github.com/RobotSkillsDevelopmentTeam/Casbot_FourSlot_Press_Shoes.git
cd Casbot_FourSlot_Press_Shoes
# 或现场已有文件夹：cd 莆田鞋厂四槽机器控制程序
```

### 3. 法奥机器人 SDK（接真机必做）

程序启动时会自动把**上一级目录**里的 `fairino` 加入模块路径：

```
试验/
├── fairino/          ← 法奥官方 Python SDK（Robot.py）
└── 莆田鞋厂四槽机器控制程序/   ← 本仓库
    └── main.py
```

若无该目录，可改为 editable 安装（路径按现场调整）：

```bash
pip install -e ../fairino
```

### 4. Python 依赖

基础包见 [requirements.txt](requirements.txt)；国内镜像命令见 §「国内镜像安装」；**完整汇总见文末「依赖清单」**。

```bash
python3 -m pip install -r requirements.txt
# Debian/Ubuntu 若 HMI 闪退，补：
sudo apt install -y libxcb-cursor0
# 中文界面字体（可选）：
sudo apt install -y fonts-noto-cjk
```

### 5. 视觉 / 真机可选依赖

| 能力 | 安装方式 | 配置位置 |
|------|----------|----------|
| YOLO 皮带/槽/鞋头 | `ultralytics` + `torch`（见镜像命令） | `models/`、`vision.shoe_vision.config` |
| Orbbec 四路相机 | 奥比中光官方 `pyorbbecsdk` | `config/default.yaml` → `cameras.cam1～4` |
| 法奥臂 | 见 §3 | `robots.robot1/2` |
| 夹爪 CAN | `python-can`（已在 requirements） | `grippers.motors` |
| 压机 | `pymodbus`（已在 requirements） | `press.*` |

模型文件：将旧工程或训练产出的 `.pt` 放到 `models/` 对应子目录（见 `algorithm_module/readme.md` 模型表）。  
皮带生产视觉 json：**[shoe_vision_config.json](shoe_vision_config.json)**（内参 / ROI / handeye 4×4，HMI 视觉页可写入）。

### 6. 首次必查配置文件

| 文件 | 作用 |
|------|------|
| **[config/default.yaml](config/default.yaml)** | **唯一参数总表**：IP、Mock、示教点、运动步、压机槽号、相机 serial |
| [shoe_vision_config.json](shoe_vision_config.json) | cam1 皮带 YOLO + 内参 + 手眼（生产用） |
| [config/roi/camN.json](config/roi/) | 各相机 ROI（HMI 视觉页写入） |
| [config/calib/](config/calib/) | 棋盘格内参、手眼采样与矩阵备份 |
| [position_config.yaml](position_config.yaml) | cam4 压杆/槽位相关（若启用 position 管线） |

默认 **多数 `use_mock: true`**，无真机也可：`python3 main.py` → 运行监控 → 初始化 → 启动 → 模拟光电。

### 7. 第一次启动与阅读顺序

```bash
python3 main.py
```

1. 读 [docs/从零看懂本程序.md](docs/从零看懂本程序.md)  
2. Mock 空跑：运行监控 → 初始化 → 启动 →「模拟光电感应到位」  
3. 接真机前： [docs/操作说明.md](docs/操作说明.md) §1 改 IP → HMI **设置 → 通信与设备** 保存 → **重启程序**  
4. 投产顺序： [docs/界面操作手册.md](docs/界面操作手册.md) §4（通信 → 负载 → 点位 → 运动 → 压机 → 视觉 → 空跑 → 自动）  
5. 软件内 **「使用说明」** 可随时搜页名、Station、文件路径  

---

## 国内镜像安装（pip / PyTorch）

以下在**项目根目录**执行。Ubuntu 若提示 externally-managed-environment，可加 `--break-system-packages` 或使用 venv。

### pip 永久换源（推荐）

```bash
mkdir -p ~/.pip
cat > ~/.pip/pip.conf << 'EOF'
[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
trusted-host = pypi.tuna.tsinghua.edu.cn
EOF
```

其他常用源（任选，替换 `index-url`）：

- 阿里云：`https://mirrors.aliyun.com/pypi/simple/`
- 中科大：`https://pypi.mirrors.ustc.edu.cn/simple/`
- 腾讯：`https://mirrors.cloud.tencent.com/pypi/simple/`

### 安装本工程基础依赖

```bash
python3 -m pip install -U pip
python3 -m pip install -r requirements.txt
```

单次临时指定清华源（不改 pip.conf）：

```bash
python3 -m pip install -r requirements.txt \
  -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### ultralytics + PyTorch（视觉 YOLO）

**CPU 版：**

```bash
python3 -m pip install ultralytics \
  -i https://pypi.tuna.tsinghua.edu.cn/simple
python3 -m pip install torch torchvision \
  -i https://pypi.tuna.tsinghua.edu.cn/simple
```

**GPU 版（CUDA 12.x 示例）：** 先到 [PyTorch 官网](https://pytorch.org) 查当前 cu 版本；国内可用清华 Anaconda 镜像装 torch：

```bash
# 示例：CUDA 12.4 轮子（以 pytorch.org 当日命令为准）
python3 -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
python3 -m pip install ultralytics -i https://pypi.tuna.tsinghua.edu.cn/simple
```

也可在 HMI **视觉 → 采图训练** 页内「安装 ultralytics」选 CPU/GPU（会调 pip）。

### 旧视觉私有包（与 Casbot_Press_Shoes 同源时）

若产线仍依赖 `casbot_yolo_point4d`、`ultralytics_obb360`、`RSDT_Simple_Automation`，按原 conda/内网文档安装；说明见 [shoe_vision_readme.md](shoe_vision_readme.md)。**新现场优先走本仓库 `algorithm_module` + `models/` 挂接，不必强行装全旧栈。**

### Orbbec SDK

按[奥比中光官方文档](https://www.orbbec.com.cn/developers/)安装 `pyorbbecsdk` 与 udev 规则；serial 填进 `config/default.yaml` 后**重启程序**。

---

## ★ 改机器人 IP（最常改）

打开 **[config/default.yaml](config/default.yaml)**：

```yaml
robots:
  robot1:
    ip: "192.168.1.105"   # 上料机器人
  robot2:
    ip: "192.168.1.115"   # 下料机器人
```

或在 HMI **设置 → 通信与设备** 修改并保存。**改 IP / 相机 serial / CAN / Mock 后请重启 `main.py`。**

各设备独立 Mock：见 [docs/操作说明.md](docs/操作说明.md)。

---

## 运行

```bash
python3 main.py
```

| 场景 | 操作 |
|------|------|
| 无真机 Mock | 运行监控 → 模式自动 → 初始化 → 启动 → 模拟光电 |
| Wayland 闪退 | `QT_QPA_PLATFORM=wayland python3 main.py` |
| X11 缺库 | `sudo apt install libxcb-cursor0` |

---

## 目录结构（开发地图）

| 路径 | 作用 |
|------|------|
| `main.py` | 程序入口 |
| [core/](core/readme.md) | 协调器、GVL 记忆、报警、三色灯、空跑屏蔽 |
| [stations/](stations/readme.md) | Station1～6 + 初始化步序 |
| [devices/](devices/readme.md) | FR5 臂、夹爪 CAN、压机 Modbus、IO |
| [vision/](vision/readme.md) | 相机 Orbbec、标定、VisionService |
| [algorithm_module/](algorithm_module/readme.md) | 视觉算法门面 `algo` |
| [visualize_module/](visualize_module/readme.md) | 相机监控取流/推演 |
| [hmi/](hmi/readme.md) | PySide6 界面、多语言、各功能页 |
| [config/](config/readme.md) | **现场参数总表**（`default.yaml`、roi、calib） |
| [models/](models/readme.md) | YOLO `.pt` 权重 |
| [docs/](docs/readme.md) | 纸质级说明（总览 / 操作 / 界面 / 夹爪） |
| [press_shoes/](press_shoes/readme.md) | 旧压机/臂样例（对照用） |
| [mobile/](mobile/readme.md) | 可选 Web 监控 |
| `各产品的使用说明/` | 电机、USB2CAN、达妙调试工具 PDF/EXE |

---

## 快速链接

| 我想… | 去看 |
|--------|------|
| 知道程序怎么跑起来 | [docs/程序总览.md](docs/程序总览.md) |
| 改 IP、CAN、压机地址 | [docs/操作说明.md](docs/操作说明.md) + `config/default.yaml` |
| 学 HMI 怎么点、手眼标定、运行快照 | [docs/界面操作手册.md](docs/界面操作手册.md)（§14.4 报警记录 · 运行快照） |
| 查视觉函数谁调谁 | [algorithm_module/readme.md](algorithm_module/readme.md) |
| 夹爪接线与试夹 | [docs/夹爪使用说明.md](docs/夹爪使用说明.md) |
| 界面里搜说明 | 运行程序 → 左侧 **Help / 使用说明**（含专章「报警记录 · 运行快照」） |

---

## 仓库

- 团队：`https://github.com/RobotSkillsDevelopmentTeam/Casbot_FourSlot_Press_Shoes.git`
- 个人备份：`https://github.com/hlx80600/-.git`（若已配置 remote）

提交前请勿把 `.env`、现场私密 IP 表、过大 `.pt`/日志误提交；`__pycache__` 建议加入 `.gitignore`。

---

## 依赖清单（汇总）

以下与 [requirements.txt](requirements.txt) 及上文「国内镜像安装」一致，便于部署时一次性核对。

### 系统与环境

| 项 | 要求 |
|----|------|
| OS | Linux（Ubuntu 22.04+ 常见） |
| Python | **3.10+**（3.12 已测） |
| 桌面（HMI） | X11 或 Wayland；缺库时 `sudo apt install -y libxcb-cursor0` |
| 中文界面（可选） | `sudo apt install -y fonts-noto-cjk` |

### 必装 Python 包（`pip install -r requirements.txt`）

| 包 | 版本 | 用途 |
|----|------|------|
| PySide6 | ≥6.5.0 | HMI 触摸屏界面 |
| PyYAML | ≥6.0 | 读取 `config/default.yaml` |
| numpy | ≥1.24.0 | 数组 / 视觉 |
| opencv-python-headless | ≥4.8.0 | 图像处理（headless，避免与 Qt 冲突） |
| scipy | ≥1.10.0 | 标定、数值计算 |
| pymodbus | ≥3.5.0 | 压鞋机 Modbus TCP |
| python-can | ≥4.3.0 | 夹爪 SocketCAN |

安装示例（国内镜像见上文 §「国内镜像安装」）：

```bash
python3 -m pip install -U pip
python3 -m pip install -r requirements.txt
```

### 接真机 / 视觉时额外安装

| 能力 | 包 / 组件 | 安装方式 | 说明 |
|------|-----------|----------|------|
| 法奥 FR5 | **fairino** | 上级目录 `../fairino` 或 `pip install -e ../fairino` | 程序启动会自动把同级 `fairino/` 加入路径 |
| Orbbec 四路相机 | **pyorbbecsdk** | [奥比中光官方文档](https://www.orbbec.com.cn/developers/) + udev | serial 写入 `config/default.yaml` |
| YOLO 推理 / 训练 | **ultralytics** + **torch** + **torchvision** | CPU/GPU 见上文 §「ultralytics + PyTorch」或 HMI 视觉页安装 | 权重放 [models/](models/readme.md) |
| 旧 Casbot 视觉栈（可选） | casbot_yolo_point4d、ultralytics_obb360、RSDT_Simple_Automation | 按旧 conda / 内网文档 | 见 [shoe_vision_readme.md](shoe_vision_readme.md)；新现场优先 `algorithm_module` |

### 标准库 / 无 pip 项

- **CAN 接口**：Linux SocketCAN（如 `can0`，1 Mbps），无需额外 Python 包（除 `python-can`）。
- **网络**：与 FR5、压机、相机同一局域网；IP 在 [config/default.yaml](config/default.yaml)。

### 按场景的最小集合

| 场景 | 需要安装 |
|------|----------|
| 仅 Mock 空跑 + HMI | requirements.txt + `libxcb-cursor0` |
| + 真机双臂 | + fairino |
| + 四路 Orbbec | + pyorbbecsdk |
| + 皮带/槽位 YOLO | + ultralytics、torch（CPU 或 GPU） |
| + 夹爪 / 压机 | requirements.txt 已含 python-can、pymodbus |
