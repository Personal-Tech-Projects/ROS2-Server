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

       

        self.current_scan = [float('inf')] * 360

        self.last_angle = 0.0



    def process_data(self):

        raw_packets = self.receiver.get_available_packets()

       

        for packet in raw_packets:

            points = decode_packet(packet)

            if not points:

                print("DEBUG: Packet rejected by decoder (Bad format/Header)")

                continue

           

            start_angle = points[0][0]



            print(f"DEBUG: Current Angle: {start_angle:.1f} | Last Angle: {self.last_angle:.1f}")



            if start_angle < 90.0 and self.last_angle > 270.0:

                self.publish_scan()

                self.current_scan = [float('inf')] * 360

               

            self.last_angle = start_angle



            for angle, distance in points:

                degree_idx = int(angle)

                self.current_scan[degree_idx] = distance



    def publish_scan(self):

        msg = LaserScan()

        msg.header.stamp = self.get_clock().now().to_msg()

        msg.header.frame_id = 'base_laser'

       

        msg.angle_min = 0.0

        msg.angle_max = 2.0 * math.pi

        msg.angle_increment = (2.0 * math.pi) / 360.0

        msg.range_min = 0.1

        msg.range_max = 8.0

        msg.ranges = self.current_scan

       

        self.publisher_.publish(msg)

