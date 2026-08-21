"""算法模块（莆田项目本地，风格对齐机器人自动化框架 algorithm_module）。

把视觉算法统一成可调用接口；工位/HMI 经 VisionService 或直接 `from algorithm_module import algo`。
未 activate 的子能力不强制加载重依赖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .results import BeltPickResult, RodOffsetResult, SlotResult, ToeAlignResult


class algorithmModule:
    """算法门面。"""

    def __init__(self) -> None:
        self._production = None
        self._commission = None
        self._tooling = None

    def activate_production(self) -> Any:
        from . import production

        self._production = production
        return production

    def activate_commission(self) -> Any:
        from . import commission

        self._commission = commission
        return commission

    def activate_tooling(self) -> Any:
        from . import tooling

        self._tooling = tooling
        return tooling

    # ---------- 生产（Station 1～5）----------

    def detect_belt_pick(
        self,
        cameras: Any,
        vis_cfg: Optional[dict],
        default_z: float,
        default_rx: float,
        default_ry: float,
    ) -> "BeltPickResult":
        prod = self._production or self.activate_production()
        return prod.detect_belt_pick(cameras, vis_cfg, default_z, default_rx, default_ry)

    def classify_slot_occupied(self, image_bgr: Any, vis_cfg: Optional[dict] = None) -> "SlotResult":
        prod = self._production or self.activate_production()
        return prod.classify_slot_occupied(image_bgr, vis_cfg)

    def classify_toe_align(self, image_bgr: Any, vis_cfg: Optional[dict] = None) -> "ToeAlignResult":
        prod = self._production or self.activate_production()
        return prod.classify_toe_align(image_bgr, vis_cfg)

    def measure_rod_offset_mm(
        self,
        cameras: Any,
        vis_cfg: Optional[dict] = None,
        image_bgr: Any = None,
    ) -> "RodOffsetResult":
        prod = self._production or self.activate_production()
        return prod.measure_rod_offset_mm(cameras, vis_cfg, image_bgr=image_bgr)

    def measure_rod_offset_tuple(
        self,
        cameras: Any,
        vis_cfg: Optional[dict] = None,
    ):
        prod = self._production or self.activate_production()
        return prod.measure_rod_offset_tuple(cameras, vis_cfg)

    def detect_belt_shoes_mock(self, vis_cfg: Optional[dict] = None) -> list:
        prod = self._production or self.activate_production()
        return prod.detect_belt_shoes_mock(vis_cfg)

    def stack_status(self) -> dict:
        prod = self._production or self.activate_production()
        return prod.stack_status()

    def model_status_text(self, vis_cfg: Optional[dict] = None) -> str:
        prod = self._production or self.activate_production()
        return prod.model_status_text(vis_cfg)

    def listed_model_paths(self, vis_cfg: Optional[dict] = None) -> list:
        prod = self._production or self.activate_production()
        return prod.listed_model_paths(vis_cfg)

    def reset_shoe_vision(self) -> None:
        prod = self._production or self.activate_production()
        prod.reset_shoe_vision()

    # ---------- 投产 ----------

    def write_intrinsics_from_calib(self, ctx, camera_id: str = "cam1") -> str:
        c = self._commission or self.activate_commission()
        return c.write_intrinsics_from_calib(ctx, camera_id)

    def write_roi_ratio_from_file(self, ctx, camera_id: str = "cam1") -> str:
        c = self._commission or self.activate_commission()
        return c.write_roi_ratio_from_file(ctx, camera_id)

    def record_handeye_sample(self, ctx, camera_id: str = "cam1", *, use_center: bool = False) -> str:
        c = self._commission or self.activate_commission()
        return c.record_handeye_sample(ctx, camera_id, use_center=use_center)

    def solve_handeye_and_write(self, ctx, camera_id: str = "cam1", *, assumed_z_mm: float = 400.0) -> str:
        c = self._commission or self.activate_commission()
        return c.solve_handeye_and_write(ctx, camera_id, assumed_z_mm=assumed_z_mm)

    def apply_belt_pick(self, ctx):
        c = self._commission or self.activate_commission()
        return c.apply_belt_pick(ctx)

    def move_robot1_to_pick(self, ctx, *, above: bool = False) -> str:
        c = self._commission or self.activate_commission()
        return c.move_robot1_to_pick(ctx, above=above)

    def checklist_lines(self, ctx) -> list:
        c = self._commission or self.activate_commission()
        return c.checklist_lines(ctx)

    def find_chessboard(self, image_bgr, cols: int, rows: int):
        c = self._commission or self.activate_commission()
        return c.find_chessboard(image_bgr, cols, rows)

    def calibrate_intrinsics(self, corners_list, image_size, cols: int, rows: int, square_mm: float):
        c = self._commission or self.activate_commission()
        return c.calibrate_intrinsics(corners_list, image_size, cols, rows, square_mm)

    def save_calib(self, camera_id: str, data: dict):
        c = self._commission or self.activate_commission()
        return c.save_calib(camera_id, data)

    def load_calib(self, camera_id: str):
        c = self._commission or self.activate_commission()
        return c.load_calib(camera_id)

    def save_roi(self, camera_id: str, roi_dict: dict):
        c = self._commission or self.activate_commission()
        return c.save_roi(camera_id, roi_dict)

    def load_roi(self, camera_id: str):
        c = self._commission or self.activate_commission()
        return c.load_roi(camera_id)

    # ---------- 采图训练 ----------

    def capture_to_slot(self, ctx, slot_id: str, cls_name: str = "", *, to_val: bool = False):
        t = self._tooling or self.activate_tooling()
        return t.capture_to_slot(ctx, slot_id, cls_name, to_val=to_val)

    def bind_model(self, ctx, slot_id: str, src, *, copy_to_default: bool = True) -> str:
        t = self._tooling or self.activate_tooling()
        return t.bind_model(ctx, slot_id, src, copy_to_default=copy_to_default)

    def link_legacy_models(self, old_dir: str = "") -> str:
        t = self._tooling or self.activate_tooling()
        return t.link_legacy_models(old_dir)

    def train_cmd(
        self, slot_id: str, *, epochs: int = 80, device: str = "cpu", batch: int | None = None
    ) -> list:
        t = self._tooling or self.activate_tooling()
        return t.train_cmd(slot_id, epochs=epochs, device=device, batch=batch)

    def cuda_train_status(self) -> dict:
        t = self._tooling or self.activate_tooling()
        return t.cuda_train_status()

    def pip_ultralytics_cmd(self, *, with_cuda: bool = False, cuda_tag: str = "cu124") -> list:
        t = self._tooling or self.activate_tooling()
        return t.pip_ultralytics_cmd(with_cuda=with_cuda, cuda_tag=cuda_tag)

    def prepare_shoe_lr_crop(self, ctx, img_bgr):
        t = self._tooling or self.activate_tooling()
        return t.prepare_shoe_lr_crop(ctx, img_bgr)

    def slots(self) -> dict:
        t = self._tooling or self.activate_tooling()
        return t.slots()


algo = algorithmModule()
