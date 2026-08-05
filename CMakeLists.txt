cmake_minimum_required(VERSION 3.8)
project(marsdog_voice_interaction)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(rclpy REQUIRED)
find_package(std_msgs REQUIRED)

rosidl_generate_interfaces(
  ${PROJECT_NAME}
  "srv/VoiceTask.srv"
)

install(
  DIRECTORY ${PROJECT_NAME}/
  DESTINATION ${PYTHON_INSTALL_DIR}/${PROJECT_NAME}
  PATTERN "srv" EXCLUDE
  PATTERN "__pycache__" EXCLUDE
  PATTERN "*.pyc" EXCLUDE
)

install(DIRECTORY config launch lib DESTINATION share/${PROJECT_NAME})

install(PROGRAMS
  scripts/voice_interaction
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
