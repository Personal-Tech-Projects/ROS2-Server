import os

from launch import LaunchDescription

from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package="slam_toolbox",
            executable="async_slam_toolbox_node",
            name="slam_toolbox",
            output="screen",
            parameters=[{
                "use_sim_time": False,
                "odom_frame": "odom",
                "base_frame": "base_link",
                "map_frame": "map",
                "scan_topic": "/scan",

                # Search beyond expected drift while limiting false matches.
                "loop_search_maximum_distance": 4.0,
                "loop_match_minimum_chain_size": 10,

                # Match the LD14P's usable range.
                "min_laser_range": 0.1,
                "max_laser_range": 8.0,

                # Keep enough keyframes and scan overlap for indoor turns.
                "minimum_travel_distance": 0.3,
                "minimum_travel_heading": 0.3,
                "minimum_time_interval": 0.2,

                # Historical match chain; separate from the live input queue.
                "scan_buffer_size": 20,
                # Async slam_toolbox requires a one-scan live queue.
                "scan_queue_size": 1,

                "map_update_interval": 2.0,

                # Conservative thresholds guard against false loop closures.
                "loop_match_minimum_response_coarse": 0.35,
                "loop_match_minimum_response_fine": 0.45,
                "do_loop_closing": True,
            }]
        )
    ])
