from fairino_robot_arm_inherit import FairinoRobotArm
import time
import yaml
import os

ARM = False

class MockPress:
	"""模拟压机控制，用于联调和测试。

	从 mock_press.yaml 加载各个压机位姿，
	通过 FairinoRobotArm 控制机械臂执行压机动作。
	"""

	def __init__(self, config_path: str | None = None) -> None:
		# 预留与机械臂/压机真实接口的集成点
		if ARM:
			self.arm: FairinoRobotArm | None = FairinoRobotArm("右机械臂", robot_ip='192.168.57.4')
			self.arm.ConnectRobotArm()
		
		# 加载配置文件
		if config_path is None:
			config_path = os.path.join(os.path.dirname(__file__), 'mock_press.yaml')
		
		self.config = self._load_config(config_path)
		self.tool = self.config.get('motion_params', {}).get('tool', 0)
		self.user = self.config.get('motion_params', {}).get('user', 0)
		self.velocity = self.config.get('motion_params', {}).get('velocity', 100)
		self.overlap = self.config.get('motion_params', {}).get('overlap', 100.0)
		self.blend_time = self.config.get('motion_params', {}).get('blend_time', -1.0)
	
	def _load_config(self, config_path: str) -> dict:
		"""从 YAML 文件加载配置。"""
		try:
			with open(config_path, 'r', encoding='utf-8') as f:
				config = yaml.safe_load(f) or {}
			print(f"[MockPress] 成功加载配置文件: {config_path}")
			return config
		except FileNotFoundError:
			print(f"[MockPress] 配置文件未找到: {config_path}，使用默认值")
			return self._default_config()
		except Exception as e:
			print(f"[MockPress] 加载配置文件异常: {e}，使用默认值")
			return self._default_config()
	
	@staticmethod
	def _default_config() -> dict:
		"""默认配置（全零位）。"""
		return {
			'left_lever_down_pose': [0.0] * 6,
			'left_lever_up_pose': [0.0] * 6,
			'right_lever_down_pose': [0.0] * 6,
			'right_lever_up_pose': [0.0] * 6,
			'home_pose': [0.0] * 6,
			'motion_params': {
				'tool': 0,
				'user': 0,
				'velocity': 100,
				'overlap': 100.0,
				'blend_time': -1.0,
			},
		}

	def move_left_lever(self, distance: float) -> int:
		if not ARM:
			return 0
		if self.arm is None:
			print("[MockPress] 机械臂未初始化，无法移动左杆")
			return -1
		left_lever_down_c = self.config.get('left_lever_down_pose', [0.0] * 6)
		left_lever_down = left_lever_down_c.copy()
		print(f"[MockPress] 左杆初始位姿: {left_lever_down}, 移动距离: {distance}")
		left_lever_down[0] += distance 
		ret = self.arm.MoveCart(desc_pos=left_lever_down, tool=self.tool, user=self.user, vel=self.velocity, ovl=self.overlap, blendT=self.blend_time)
		if ret != 0:
			print(f"[MockPress] 机械臂移动到左杆目标失败，返回值: {ret}")
			return ret
		print(f"[MockPress] 左杆调整完成, 距离: {distance}")
		return 0

	def move_left_up(self) -> int:
		if not ARM:
			return 0
		"""左侧压机杠杆抬起复位：机械臂移动到左杆复位位。"""
		if self.arm is None:
			print("[MockPress] 机械臂未初始化，无法抬起左杆")
			return -1

		left_lever_up = self.config.get('left_lever_up_pose', [0.0] * 6)
		ret = self.arm.MoveCart(desc_pos=left_lever_up, tool=self.tool, user=self.user, vel=self.velocity, ovl=self.overlap, blendT=self.blend_time)
		if ret != 0:
			print(f"[MockPress] 机械臂移动到左杆复位位失败，返回值: {ret}")
			return ret
		print("[MockPress] 左杆抬起复位完成")
		return 0

	def move_right_lever(self, distance: float) -> int:
		if not ARM:
			return 0
		"""右侧压机杠杆：通过机械臂移动到指定下压点位。"""
		if self.arm is None:
			print("[MockPress] 机械臂未初始化，无法移动右杆")
			return -1

		right_lever_down = self.config.get('right_lever_down_pose', [0.0] * 6)
		right_lever_down[0] += distance  
		ret = self.arm.MoveCart(desc_pos=right_lever_down, tool=self.tool, user=self.user, vel=self.velocity, ovl=self.overlap, blendT=self.blend_time)
		if ret != 0:
			print(f"[MockPress] 机械臂移动到目标上方失败，返回值: {ret}")
			return ret
		print(f"[MockPress] 右杆调整完成, 距离: {distance}")
		return 0

	def move_right_up(self) -> int:
		if not ARM:
			return 0
		"""右侧压机杠杆抬起复位：机械臂移动到右杆复位位。"""
		if self.arm is None:
			print("[MockPress] 机械臂未初始化，无法抬起右杆")
			return -1

		right_lever_up = self.config.get('right_lever_up_pose', [0.0] * 6)
		ret = self.arm.MoveCart(desc_pos=right_lever_up, tool=self.tool, user=self.user, vel=self.velocity, ovl=self.overlap, blendT=self.blend_time)
		if ret != 0:
			print(f"[MockPress] 机械臂移动到右杆复位位失败，返回值: {ret}")
			return ret
		print("[MockPress] 右杆抬起复位完成")
		return 0

	def move_home(self) -> int:
		if not ARM:
			return 0
		"""压机整体回到初始/home 位置。"""
		if self.arm is None:
			print("[MockPress] 机械臂未初始化，无法执行回 home")
			return -1

		home_pose = self.config.get('home_pose', [0.0] * 6)
		ret = self.arm.MoveCart(desc_pos=home_pose, tool=self.tool, user=self.user, vel=self.velocity, ovl=self.overlap, blendT=self.blend_time)
		if ret != 0:
			print(f"[MockPress] 机械臂回到 home 位失败，返回值: {ret}")
			return ret
		print("[MockPress] 压机回到初始位置 (home) 完成")
		return 0


if __name__ == "__main__":
	import sys
	from pathlib import Path
	
	# 确保项目根目录在 Python 路径中
	_project_root = Path(__file__).resolve().parents[2]
	if str(_project_root) not in sys.path:
		sys.path.insert(0, str(_project_root))
	
	print("=" * 50)
	print("MockPress 测试程序")
	print("=" * 50)
	
	try:
		# 创建 MockPress 实例
		print("\n[初始化] 创建 MockPress 实例...")
		manager = MockPress()
		print("[初始化] MockPress 实例创建成功")
		
		# 测试左杆复位
		print("\n[测试1] 左杆抬起复位...")
		ret = manager.move_left_up()
		if ret != 0:
			print(f"[错误] 左杆复位失败，返回值: {ret}")
		time.sleep(1)
		
		# 测试左杆移动
		print("\n[测试2] 左杆下压 (距离: 5mm)...")
		ret = manager.move_left_lever(5)
		if ret != 0:
			print(f"[错误] 左杆移动失败，返回值: {ret}")
		time.sleep(1)
		
		
		# 测试回 home
		print("\n[测试5] 压机回到 home 位...")
		ret = manager.move_home()
		if ret != 0:
			print(f"[错误] 回 home 失败，返回值: {ret}")
		time.sleep(1)
		
		print("\n" + "=" * 50)
		print("所有测试完成！")
		print("=" * 50)
		
	except Exception as e:
		print(f"\n[异常] MockPress 测试异常: {e}")
		import traceback
		traceback.print_exc()
		sys.exit(1)