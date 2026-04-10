import rclpy
from rclpy.node import Node
from nav_msgs.msg import OccupancyGrid
import numpy as np
import cv2
import json

class MapExtractor(Node):
    def __init__(self):
        super().__init__('map_extractor')
        
        # Subscribe to the SLAM map topic
        self.subscription = self.create_subscription(
            OccupancyGrid,
            '/map',
            self.map_callback,
            10
        )
        self.get_logger().info("Map Extractor active! Waiting for /map data...")

    def map_callback(self, msg):
        self.get_logger().info("Received map update. Generating image...")

        # ==========================================
        # 1. EXTRACT & SAVE METADATA
        # ==========================================
        # We need this math later to convert pixels back to meters
        metadata = {
            "resolution": msg.info.resolution,
            "origin_x": msg.info.origin.position.x,
            "origin_y": msg.info.origin.position.y,
            "width": msg.info.width,
            "height": msg.info.height
        }
        
        with open('map_metadata.json', 'w') as f:
            json.dump(metadata, f, indent=4)

        # ==========================================
        # 2. CONVERT ARRAY TO IMAGE
        # ==========================================
        # msg.data is a flat 1D list. Reshape it into a 2D grid (height x width)
        map_data = np.array(msg.data, dtype=np.int8).reshape((msg.info.height, msg.info.width))

        # Create a blank RGB image array
        img = np.zeros((msg.info.height, msg.info.width, 3), dtype=np.uint8)

        # Color the pixels based on the SLAM confidence values
        img[map_data == -1]  = [127, 127, 127]  # Gray  = Unknown territory
        img[map_data == 0]   = [255, 255, 255]  # White = Safe free space
        img[map_data == 100] = [0, 0, 0]        # Black = Solid wall / obstacle

        # ==========================================
        # 3. FIX ORIENTATION & SAVE
        # ==========================================
        # ROS 2 maps start with the origin (0,0) at the bottom-left.
        # Image files (and LLMs) expect the origin at the top-left. 
        # We flip the image vertically so it doesn't look upside down to the LLM.
        img_flipped = cv2.flip(img, 0)

        cv2.imwrite('current_map.png', img_flipped)
        self.get_logger().info("Successfully saved current_map.png and map_metadata.json")

def main(args=None):
    rclpy.init(args=args)
    node = MapExtractor()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()