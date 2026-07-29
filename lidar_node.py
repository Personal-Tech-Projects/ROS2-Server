import math
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from udp_receiver import LidarUdpReceiver
from ld14p_decoder import decode_packet

class LidarBridgeNode(Node):
    def __init__(self):
        super().__init__('lidar_bridge')
        self.publisher_ = self.create_publisher(LaserScan, 'scan', 10)
        self.receiver = LidarUdpReceiver(port=5006)
        self.timer = self.create_timer(0.01, self.process_data)
        
        # === FIX 1: Initialize array with 0.0 (Ignored by rf2o) ===
        self.current_scan = [0.0] * 360
        self.last_angle = 0.0
        
        ### FIX 2: Create a variable to hold the start time of the scan ###
        self.current_scan_time = self.get_clock().now().to_msg()

    def process_data(self):
        raw_packets = self.receiver.get_available_packets()
        
        # 1. Prove the packets are actually entering Python
        if len(raw_packets) > 0:
            self.get_logger().info(f"Received {len(raw_packets)} raw UDP packets this cycle.")
        
        for packet in raw_packets:
            points = decode_packet(packet)
            if not points:
                # 2. Use ROS2 logger instead of invisible print()
                self.get_logger().warn("Packet rejected by decoder (Bad format/Header/Checksum)")
                continue
            
            start_angle = points[0][0]

            # 3. Print the angle to see if it is spinning backwards
            self.get_logger().info(f"Decoder Success! Current start angle: {start_angle:.1f}")

            # Check if we wrapped around 360 degrees
            if start_angle < 90.0 and self.last_angle > 270.0:
                self.publish_scan()
                self.current_scan = [0.0] * 360
                self.current_scan_time = self.get_clock().now().to_msg() 
                
            self.last_angle = start_angle

            for angle, distance in points:
                # Modulo 360 prevents IndexOutOfBounds if angle hits 360.0 exactly
                degree_idx = int(angle) % 360 
                self.current_scan[degree_idx] = distance

    def publish_scan(self):
        msg = LaserScan()
        
        ### FIX 3: Use the saved time from the start of the rotation, NOT the current time ###
        msg.header.stamp = self.current_scan_time
        
        msg.header.frame_id = 'base_laser' 
        
        msg.angle_min = 0.0
        msg.angle_max = 2.0 * math.pi
        msg.angle_increment = (2.0 * math.pi) / 360.0
        
        # Required for rf2o velocity calculations
        msg.scan_time = 0.1 
        msg.time_increment = msg.scan_time / 360.0
        
        msg.range_min = 0.1
        msg.range_max = 8.0
        msg.ranges = self.current_scan
        
        msg.intensities = [0.0] * 360

        self.publisher_.publish(msg)
        
        self.get_logger().info('Successfully published 360-degree LaserScan!')