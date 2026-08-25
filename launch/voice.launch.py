from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    share = FindPackageShare("marsdog_voice_interaction")
    config_path = LaunchConfiguration("config_path")
    log_level = LaunchConfiguration("log_level")
    log_dir = LaunchConfiguration("log_dir")
    return LaunchDescription([
        DeclareLaunchArgument(
            "config_path",
            default_value=PathJoinSubstitution([share, "config", "voice.yaml"]),
        ),
        DeclareLaunchArgument(
            "log_level",
            default_value="",
            description="Override logging.level from the selected config",
        ),
        DeclareLaunchArgument(
            "log_dir",
            default_value="",
            description="Override logging.dir from the selected config",
        ),
        Node(
            package="marsdog_voice_interaction",
            executable="voice_interaction",
            name="voice_interaction",
            output="screen",
            parameters=[{
                "config_path": config_path,
                "log_level": log_level,
                "log_dir": log_dir,
            }],
        ),
    ])
