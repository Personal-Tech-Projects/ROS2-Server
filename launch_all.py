import os
from launch import LaunchDescription
from launch.actions import ExecuteProcess, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node  # Added this import

def generate_launch_description():
    return LaunchDescription([
       # 1. Start your custom Python IMU Bridge (Replaces micro-ROS)
        ExecuteProcess(
            cmd=['python3', 'imu_bridge.py'],
            output='screen'
        ),

        # 2. Start your custom Python Lidar Bridge
        ExecuteProcess(
            cmd=['python3', 'main.py'],
            output='screen'
        ),

        # 3. Static Transform: Tells the EKF where the IMU is located
        # IMU is at the robot's center, coincident with base_link.
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='imu_static_tf',
            arguments=['0.0', '0.0', '0.0', '0.0', '0.0', '0.0', 'base_link', 'imu_link']
        ),
        
        # 4. Start the Odometry and TF tree
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource('launch_odometry.py')
        ),
        
        # 5. Start SLAM Toolbox
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource('launch_slam.py')
        ),
        
        # 6. Open RViz visualizer
        ExecuteProcess(
            cmd=['rviz2'],
            output='screen'
        )
    ])