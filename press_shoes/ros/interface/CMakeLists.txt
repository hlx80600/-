cmake_minimum_required(VERSION 3.8)
project(press_shoes_interface)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "srv/RobotArmCommand.srv"
  "srv/RobotGripperCommand.srv"
)

ament_package()
