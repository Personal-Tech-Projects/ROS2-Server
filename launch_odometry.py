import os
from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    # Assuming ekf.yaml is in the same directory you launch from
    ekf_config_path = os.path.abspath('ekf.yaml')

    return LaunchDescription([
        # 1. Static Transform for the LD14P LiDAR
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='base_to_laser_broadcaster',
            arguments=['0', '0', '0', '0', '0', '0', 'base_link', 'base_laser']
        ),
        
        # 2. rf2o Laser Odometry Node
        Node(
            package='rf2o_laser_odometry',
            executable='rf2o_laser_odometry_node',
            name='rf2o_laser_odometry',
            output='screen',
            parameters=[{
                'laser_scan_topic' : '/scan',
                # === FIX APPLIED HERE: Publish to a hidden topic to avoid conflict with EKF ===
                'odom_topic' : '/odom_rf2o', 
                # === FIX APPLIED HERE: Let EKF handle the TF Tree ===
                'publish_tf' : False,                 
                'base_frame_id' : 'base_link',
                'odom_frame_id' : 'odom',
                'init_pose_from_topic' : '',
                'freq' : 20.0
            }],
            # === FIX APPLIED HERE: Remap the internal topic ===
            remappings=[
                ('/odom', '/odom_rf2o'),
            ]
        ),

        # === FIX APPLIED HERE: 3. The new Extended Kalman Filter (EKF) ===
        Node(
            package='robot_localization',
            executable='ekf_node',
            name='ekf_filter_node',
            output='screen',
            parameters=[ekf_config_path]
        )
    ])