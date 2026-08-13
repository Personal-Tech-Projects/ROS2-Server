import os
from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    code_dir = os.path.dirname(os.path.abspath(__file__))
    ekf_config_path = os.path.join(code_dir, 'ekf.yaml')

    return LaunchDescription([
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='base_to_laser_broadcaster',
            arguments=[
                '--x', '0.0', '--y', '0.0', '--z', '0.0',
                '--yaw', '0.0', '--pitch', '0.0', '--roll', '0.0',
                '--frame-id', 'base_link', '--child-frame-id', 'base_laser',
            ]
        ),
        
        Node(
            package='rf2o_laser_odometry',
            executable='rf2o_laser_odometry_node',
            name='rf2o_laser_odometry',
            output='screen',
            parameters=[{
                'laser_scan_topic' : '/scan',
                'odom_topic' : '/odom_rf2o',
                # EKF is the sole publisher of odom -> base_link.
                'publish_tf' : False,
                'base_frame_id' : 'base_link',
                'odom_frame_id' : 'odom',
                'init_pose_from_topic' : '',
                # LiDAR is about 6 Hz; 10 Hz consumes every scan without noise.
                'freq' : 10.0
            }],
            remappings=[
                ('/odom', '/odom_rf2o'),
            ]
        ),

        Node(
            package='robot_localization',
            executable='ekf_node',
            name='ekf_filter_node',
            output='screen',
            parameters=[ekf_config_path]
        )
    ])
