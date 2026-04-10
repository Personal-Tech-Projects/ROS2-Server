import os

from launch import LaunchDescription

from launch_ros.actions import Node



def generate_launch_description():

    return LaunchDescription([

        # 1. Static Transform for the LD14P LiDAR

        # This tells the system where the LiDAR is relative to the center of the robot

        Node(

            package='tf2_ros',

            executable='static_transform_publisher',

            name='base_to_laser_broadcaster',

            arguments=['0', '0', '0', '0', '0', '0', 'base_link', 'base_laser']

        ),

       

        # 2. Odometry Node (Moved from your SLAM file)

        Node(

            package='rf2o_laser_odometry',

            executable='rf2o_laser_odometry_node',

            name='rf2o_laser_odometry',

            output='screen',

            parameters=[{

                'laser_scan_topic' : '/scan',        

                'odom_topic' : '/odom',              

                'publish_tf' : True,                

                'base_frame_id' : 'base_link',  # Updated to connect with the transform above

                'odom_frame_id' : 'odom',            

                'init_pose_from_topic' : '',

                'freq' : 20.0                        

            }]

        )

    ])

