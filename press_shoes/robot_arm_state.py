from enum import Enum
from threading import RLock
from typing import Callable, Optional, Tuple

class PutInArmStatus(Enum):
    PLACE_LEFT_SHOE = "放左鞋"
    PLACE_RIGHT_SHOE = "放右鞋"
    SET_PRESS_MACHINE = "设置压机"
    SET_PRESS_MACHINE_LEFT = "设置压机左槽"
    SET_PRESS_MACHINE_RIGHT = "设置压机右槽"
    GRAB_PENDING_SHOES = "抓取待处理的鞋"
    ERROR = "错误状态"
    EXIT_FAR_USE_NEAR = "退出远端占用近端"
    EXIT_WORKFLOW = "退出流程"
    UNKNOWN = "未知状态"

    

class TakeOutArmStatus(Enum):
    FETCH_TASK = "获取取鞋任务"
    PICK_RIGHT_SHOE = "取右鞋"
    PICK_LEFT_SHOE = "取左鞋"
    PLACE_RIGHT_SHOE = "放右鞋"
    PLACE_LEFT_SHOE = "放左鞋"
    ERROR = "错误状态"
    EXIT_FAR_USE_NEAR = "退出远端占用近端"
    EXIT_WORKFLOW = "退出流程"
    UNKNOWN = "未知状态"


class PutInArmState:
    def __init__(
        self,
        current: PutInArmStatus,
        next_state: Optional[PutInArmStatus],
        notifier: Optional[Callable[[], None]] = None,
    ) -> None:
        self._current = current
        self._next = next_state
        self._lock = RLock()
        self._notifier = notifier

    def _notify(self) -> None:
        if self._notifier:
            self._notifier()

    def set_current(self, state: PutInArmStatus) -> None:
        with self._lock:
            self._current = state
            self._notify()

    def set_next(self, state: Optional[PutInArmStatus]) -> None:
        with self._lock:
            self._next = state
            self._notify()

    def advance(self, next_state: Optional[PutInArmStatus]) -> Tuple[PutInArmStatus, Optional[PutInArmStatus]]:
        with self._lock:
            if next_state:
                self._current = next_state
            self._notify()
            return self._current, self._next

    def get_state(self) -> Tuple[PutInArmStatus, Optional[PutInArmStatus]]:
        with self._lock:
            return self._current, self._next


class TakeOutArmState:
    def __init__(
        self,
        current: TakeOutArmStatus,
        next_state: Optional[TakeOutArmStatus],
        notifier: Optional[Callable[[], None]] = None,
    ) -> None:
        self._current = current
        self._next = next_state
        self._lock = RLock()
        self._notifier = notifier

    def _notify(self) -> None:
        if self._notifier:
            self._notifier()

    def set_current(self, state: TakeOutArmStatus) -> None:
        with self._lock:
            self._current = state
            self._notify()

    def set_next(self, state: Optional[TakeOutArmStatus]) -> None:
        with self._lock:
            self._next = state
            self._notify()

    def advance(self, next_state: Optional[TakeOutArmStatus]) -> Tuple[TakeOutArmStatus, Optional[TakeOutArmStatus]]:
        with self._lock:
            if next_state:
                self._current = next_state
            self._notify()
            return self._current, self._next

    def get_state(self) -> Tuple[TakeOutArmStatus, Optional[TakeOutArmStatus]]:
        with self._lock:
            return self._current, self._next
ArmStateView = Tuple[Tuple[PutInArmStatus, Optional[PutInArmStatus]], Tuple[TakeOutArmStatus, Optional[TakeOutArmStatus]]]


def _test_robot_arm_state():
    left_arm = PutInArmState(PutInArmStatus.GRAB_PENDING_SHOES, PutInArmStatus.PLACE_LEFT_SHOE)
    right_arm = TakeOutArmState(TakeOutArmStatus.FETCH_TASK, TakeOutArmStatus.PICK_RIGHT_SHOE)

    def left_worker():
        left_steps = [
            (PutInArmStatus.PLACE_RIGHT_SHOE, (PutInArmStatus.PLACE_LEFT_SHOE, PutInArmStatus.PLACE_RIGHT_SHOE)),
            (PutInArmStatus.SET_PRESS_MACHINE, (PutInArmStatus.PLACE_RIGHT_SHOE, PutInArmStatus.SET_PRESS_MACHINE)),
            (PutInArmStatus.PLACE_RIGHT_SHOE, (PutInArmStatus.SET_PRESS_MACHINE, PutInArmStatus.PLACE_RIGHT_SHOE)),
        ]
        for next_state, expected in left_steps:
            cur, nxt = left_arm.advance(next_state)
            print(f"[Left] Current: {cur}, Next: {nxt}")
            assert (cur, nxt) == expected, f"Left arm advance mismatch: {(cur, nxt)} != {expected}"
            time.sleep(0.05)

    def right_worker():
        right_steps = [
            (TakeOutArmStatus.PICK_LEFT_SHOE, (TakeOutArmStatus.PICK_RIGHT_SHOE, TakeOutArmStatus.PICK_LEFT_SHOE)),
            (TakeOutArmStatus.PLACE_RIGHT_SHOE, (TakeOutArmStatus.PICK_LEFT_SHOE, TakeOutArmStatus.PLACE_RIGHT_SHOE)),
            (TakeOutArmStatus.PICK_LEFT_SHOE, (TakeOutArmStatus.PLACE_RIGHT_SHOE, TakeOutArmStatus.PICK_LEFT_SHOE)),
        ]
        for next_state, expected in right_steps:
            cur, nxt = right_arm.advance(next_state)
            print(f"[Right] Current: {cur}, Next: {nxt}")
            assert (cur, nxt) == expected, f"Right arm advance mismatch: {(cur, nxt)} != {expected}"
            time.sleep(0.05)

    t1 = threading.Thread(target=left_worker)
    t2 = threading.Thread(target=right_worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    final: ArmStateView = (left_arm.get_state(), right_arm.get_state())
    print("Final Arm States:", final)

    # 期望值
    expected_left = (PutInArmStatus.SET_PRESS_MACHINE, PutInArmStatus.PLACE_RIGHT_SHOE)
    expected_right = (TakeOutArmStatus.PLACE_RIGHT_SHOE, TakeOutArmStatus.PICK_LEFT_SHOE)
    assert final[0] == expected_left, f"Left arm state error: {final[0]} != {expected_left}"
    assert final[1] == expected_right, f"Right arm state error: {final[1]} != {expected_right}"
    print("Test passed!")


if __name__ == "__main__":
    import threading
    import time
    _test_robot_arm_state()