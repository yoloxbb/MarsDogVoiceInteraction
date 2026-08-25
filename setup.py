from glob import glob

from setuptools import find_packages, setup


package_name = "marsdog_voice_interaction"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["tests"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config", glob("config/*")),
        ("share/" + package_name + "/launch", glob("launch/*.py")),
    ],
    install_requires=[
        "setuptools",
        "fastapi>=0.115,<1",
        "uvicorn>=0.30,<1",
        "python-multipart>=0.0.9,<1",
    ],
    zip_safe=True,
    maintainer="MarsDog Voice Team",
    maintainer_email="noreply@marsdog.dev",
    description="MarsDog voice interaction ROS2 node",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "marsdog-voice-interaction = marsdog_voice_interaction.main:main",
        ],
    },
)
