import os
from launch import LaunchDescription
from launch.actions import ExecuteProcess, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node  # Added this import

def generate_launch_description():
    return LaunchDescription([
        # 1. Start the micro-ROS Agent (Your Bridge to the ESP32)
        Node(
            package='micro_ros_agent',
            executable='micro_ros_agent',
            name='micro_ros_agent',
            output='screen',
            arguments=['udp4', '--port', '8888']
        ),

        # 2. Start your custom Python Lidar Bridge
        ExecuteProcess(
            cmd=['python3', 'main.py'],
            output='screen'
        ),
        
        # 3. Start the Odometry and TF tree
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource('launch_odometry.py')
        ),
        
        # 4. Start SLAM Toolbox
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource('launch_slam.py')
        ),
        
        # 5. Open RViz visualizer
        ExecuteProcess(
            cmd=['rviz2'],
            output='screen'
        )
    ])