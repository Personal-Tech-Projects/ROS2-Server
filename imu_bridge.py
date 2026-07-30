import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Imu
import socket
import threading

class ImuBridge(Node):
    def __init__(self):
        super().__init__('imu_bridge')
        # Create the publisher that your EKF will listen to
        self.publisher_ = self.create_publisher(Imu, 'imu/data', 10)
        
        # Setup the isolated UDP Socket for port 8888
        self.udp_ip = "0.0.0.0"
        self.udp_port = 8888
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.udp_ip, self.udp_port))
        
        self.get_logger().info(f"Listening for IMU data on UDP port {self.udp_port}...")
        
        # Keep track of the latest readings so we can bundle them into one message
        self.latest_rot = [0.0, 0.0, 0.0, 1.0] # x, y, z, w
        self.latest_acc = [0.0, 0.0, 0.0]      # x, y, z
        
        # Spin up a dedicated thread just for reading the socket
        self.listener_thread = threading.Thread(target=self.udp_listener_loop, daemon=True)
        self.listener_thread.start()

    def udp_listener_loop(self):
        while True:
            # recvfrom() grabs exactly one UDP packet at a time, preventing them from bleeding together
            data, _ = self.sock.recvfrom(1024)
            message = data.decode('utf-8').strip()
            
            # Split the string: e.g., "ROT,0.6153,-0.0023,-0.0318,0.7877"
            parts = message.split(',')
            
            if len(parts) == 0:
                continue
                
            try:
                if parts[0] == 'ROT' and len(parts) == 5:
                    # BNO08x sends: real(w), i(x), j(y), k(z)
                    w, x, y, z = map(float, parts[1:])
                    print(f"ROT -> w: {w}, x: {x}, y: {y}, z: {z}")
                    self.latest_rot = [x, y, z, w]
                    self.publish_imu()
                    
                elif parts[0] == 'ACC' and len(parts) == 4:
                    # BNO08x sends: x, y, z
                    x, y, z = map(float, parts[1:])
                    print(f"ACC -> x: {x}, y: {y}, z: {z}")
                    self.latest_acc = [x, y, z]
                    self.publish_imu()
            except ValueError:
                self.get_logger().warn(f"Failed to parse IMU string: {message}")

    def publish_imu(self):
        msg = Imu()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'imu_link'
        
        # Populate Orientation
        msg.orientation.x = self.latest_rot[0]
        msg.orientation.y = self.latest_rot[1]
        msg.orientation.z = self.latest_rot[2]
        msg.orientation.w = self.latest_rot[3]
        
        # Populate Acceleration
        msg.linear_acceleration.x = self.latest_acc[0]
        msg.linear_acceleration.y = self.latest_acc[1]
        msg.linear_acceleration.z = self.latest_acc[2]

        # Override the 0.0 defaults so the EKF accepts the data
        msg.orientation_covariance[0] = 0.01
        msg.orientation_covariance[4] = 0.01
        msg.orientation_covariance[8] = 0.01
        
        msg.angular_velocity_covariance[0] = 0.01
        msg.angular_velocity_covariance[4] = 0.01
        msg.angular_velocity_covariance[8] = 0.01
        
        msg.linear_acceleration_covariance[0] = 0.01
        msg.linear_acceleration_covariance[4] = 0.01
        msg.linear_acceleration_covariance[8] = 0.01
        
        # (Optional) If you enable the Gyro on the ESP32 later, you would add angular_velocity here
        
        self.publisher_.publish(msg)

def main(args=None):
    rclpy.init(args=args)
    node = ImuBridge()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()