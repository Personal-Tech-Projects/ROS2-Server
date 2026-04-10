import rclpy

from lidar_node import LidarBridgeNode



def main(args=None):

    # Turn on the ROS 2 Engine

    rclpy.init(args=args)

   

    # Create our custom Node

    node = LidarBridgeNode()



    if node is None:

        print("CRITICAL ERROR: Node failed to instantiate!")

        return



    # oes ROS 2 recognize it? Ask the node for its official network name.

    node_name = node.get_name()

   

    # Use the ROS 2 logger instead of print.

    # If this works, the node is 100% alive and hooked into the system.

    node.get_logger().info(f'SUCCESS: [{node_name}] has been created and is active.')

    node.get_logger().info('Listening for ESP32 UDP packets on port 5006...')

   

    # Spin the Node (keep it running forever)

    rclpy.spin(node)

   

    # Clean up if the user hits Ctrl+C

    node.destroy_node()

    rclpy.shutdown()



if __name__ == '__main__':

    main()