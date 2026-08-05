"""MarsDog voice interaction ROS2 package."""

from pkgutil import extend_path


# The ROS2 build generates ``marsdog_voice_interaction.srv`` in the install
# overlay while uv keeps the Python sources editable in the project tree.
# Extend the package path so both portions are importable at the same time.
__path__ = extend_path(__path__, __name__)

__version__ = "0.1.0"
