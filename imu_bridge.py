import math
import socket
import threading

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Imu


class ImuBridge(Node):
    def __init__(self):
        super().__init__('imu_bridge')
        self.publisher_ = self.create_publisher(
            Imu, 'imu/data', qos_profile_sensor_data)

        self.udp_port = 8888
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        self.sock.bind(('0.0.0.0', self.udp_port))

        self.get_logger().info(
            f"Listening for IMU data on UDP port {self.udp_port}...")

        self.latest_acc = [0.0, 0.0, 0.0]
        self._stats = dict(
            rx=0, rot=0, acc=0, published=0,
            decode_fail=0, badfmt=0, yaw_guard=0)
        self.create_timer(1.0, self._report)

        self.listener_thread = threading.Thread(
            target=self.udp_listener_loop, daemon=True)
        self.listener_thread.start()

    def _report(self):
        stats = self._stats
        if stats['rx']:
            self.get_logger().info(
                "imu: %d pkt/s rot=%d acc=%d published=%d "
                "decode_fail=%d badformat=%d"
                % (stats['rx'], stats['rot'], stats['acc'],
                   stats['published'], stats['decode_fail'], stats['badfmt']))
        else:
            self.get_logger().warn("imu: NO UDP packets arriving on 8888")

        for key in stats:
            stats[key] = 0

    def udp_listener_loop(self):
        while rclpy.ok():
            try:
                data, _ = self.sock.recvfrom(1024)
            except OSError as error:
                self.get_logger().error("imu recvfrom failed: %r" % (error,))
                continue

            self._stats['rx'] += 1
            try:
                message = data.decode('utf-8').strip()
            except UnicodeDecodeError:
                self._stats['decode_fail'] += 1
                continue

            parts = message.split(',')
            try:
                if parts[0] == 'ROT' and len(parts) == 5:
                    # BNO08x sends real(w), i(x), j(y), k(z).
                    w, x, y, z = map(float, parts[1:])
                    norm = math.sqrt(x*x + y*y + z*z + w*w)
                    if not math.isfinite(norm) or norm < 0.5:
                        self._stats['badfmt'] += 1
                        continue

                    quaternion = [x / norm, y / norm, z / norm, w / norm]
                    self._stats['rot'] += 1
                    self.publish_imu(quaternion)

                elif parts[0] == 'ACC' and len(parts) == 4:
                    self.latest_acc = list(map(float, parts[1:]))
                    self._stats['acc'] += 1
                    # Publish only on ROT packets to avoid duplicate orientation.
                else:
                    self._stats['badfmt'] += 1
            except (ValueError, IndexError):
                self._stats['badfmt'] += 1

    def publish_imu(self, quaternion):
        message = Imu()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = 'imu_link'

        message.orientation.x = quaternion[0]
        message.orientation.y = quaternion[1]
        message.orientation.z = quaternion[2]
        message.orientation.w = quaternion[3]
        message.orientation_covariance[0] = 0.01
        message.orientation_covariance[4] = 0.01
        message.orientation_covariance[8] = 0.01

        # -1 marks angular velocity as unavailable in sensor_msgs/Imu.
        message.angular_velocity_covariance[0] = -1.0

        message.linear_acceleration.x = self.latest_acc[0]
        message.linear_acceleration.y = self.latest_acc[1]
        message.linear_acceleration.z = self.latest_acc[2]
        message.linear_acceleration_covariance[0] = 0.01
        message.linear_acceleration_covariance[4] = 0.01
        message.linear_acceleration_covariance[8] = 0.01

        self.publisher_.publish(message)
        self._stats['published'] += 1


def main(args=None):
    rclpy.init(args=args)
    node = ImuBridge()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
