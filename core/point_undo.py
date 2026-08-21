"""点位添加/删除/覆盖的撤回栈（HMI 共用）。"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional


_MAX = 30


class PointUndoStack:
    """记录对 points.{robot}.{key} 的增删改，支持逐步撤回。"""

    def __init__(self) -> None:
        self._stack: List[Dict[str, Any]] = []

    def clear(self) -> None:
        self._stack.clear()

    def can_undo(self) -> bool:
        return bool(self._stack)

    def depth(self) -> int:
        return len(self._stack)

    def push_add(self, robot_key: str, point_key: str, after: Dict) -> None:
        self._push(
            {
                "op": "add",
                "robot_key": robot_key,
                "point_key": point_key,
                "before": None,
                "after": copy.deepcopy(after),
            }
        )

    def push_delete(self, robot_key: str, point_key: str, before: Dict) -> None:
        self._push(
            {
                "op": "delete",
                "robot_key": robot_key,
                "point_key": point_key,
                "before": copy.deepcopy(before),
                "after": None,
            }
        )

    def push_update(
        self, robot_key: str, point_key: str, before: Optional[Dict], after: Dict
    ) -> None:
        self._push(
            {
                "op": "update",
                "robot_key": robot_key,
                "point_key": point_key,
                "before": copy.deepcopy(before) if before else None,
                "after": copy.deepcopy(after),
            }
        )

    def _push(self, item: Dict[str, Any]) -> None:
        self._stack.append(item)
        if len(self._stack) > _MAX:
            self._stack.pop(0)

    def undo(self, cfg: Dict[str, Any]) -> str:
        """
        撤回一步并写回 cfg['points']。
        返回给人看的说明；无可撤回时抛 ValueError。
        """
        if not self._stack:
            raise ValueError("没有可撤回的操作")
        item = self._stack.pop()
        rk = str(item["robot_key"])
        pk = str(item["point_key"])
        pts = cfg.setdefault("points", {}).setdefault(rk, {})
        op = item["op"]
        if op == "add":
            # 新增 → 删掉
            if pk in pts:
                del pts[pk]
            return f"已撤回新增：points.{rk}.{pk}"
        if op == "delete":
            # 删除 → 恢复
            before = item.get("before")
            if isinstance(before, dict):
                pts[pk] = copy.deepcopy(before)
            return f"已撤回删除：points.{rk}.{pk}"
        if op == "update":
            before = item.get("before")
            if before is None:
                # 原本不存在却写成 update 时，撤回=删除
                pts.pop(pk, None)
                return f"已撤回覆盖为新建：已删除 points.{rk}.{pk}"
            pts[pk] = copy.deepcopy(before)
            return f"已撤回覆盖：points.{rk}.{pk}"
        raise ValueError(f"未知撤回类型: {op}")
