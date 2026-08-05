from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    share = FindPackageShare("marsdog_voice_interaction")
    config_path = LaunchConfiguration("config_path")
    return LaunchDescription([
        DeclareLaunchArgument(
            "config_path",
            default_value=PathJoinSubstitution([share, "config", "voice.yaml"]),
        ),
        Node(
            package="marsdog_voice_interaction",
            executable="voice_interaction",
            name="voice_interaction",
            output="screen",
            parameters=[{"config_path": config_path}],
        ),
    ])
