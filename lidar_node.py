import math
import statistics

from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Imu, LaserScan

from ld14p_decoder import decode_packet_with_metadata
from udp_receiver import LidarUdpReceiver


SCAN_BEAMS = 360
MIN_SCAN_FILL = 200
SENSOR_TIMESTAMP_WRAP_MS = 30000
IMU_STALE_NS = 500_000_000


class LidarBridgeNode(Node):
    def __init__(self):
        super().__init__('lidar_bridge')
        self.publisher_ = self.create_publisher(
            LaserScan, 'scan', qos_profile_sensor_data)
        self.imu_subscription = self.create_subscription(
            Imu, 'imu/data', self._on_imu, qos_profile_sensor_data)
        self.last_imu_ns = None
        self.imu_gate_active = True
        self.receiver = LidarUdpReceiver(port=5006)
        self.timer = self.create_timer(0.01, self.process_data)

        self.current_scan = [math.nan] * SCAN_BEAMS
        self.last_raw_start_angle = None
        self.scan_start_stamp_ns = None
        self.scan_period = 1.0 / 6.0
        self.rotation_speeds = []

        # Preserve sensor spacing while mapping its wrapping clock to ROS time.
        self.sensor_last_ms = None
        self.sensor_elapsed_ms = 0
        self.sensor_last_ros_ns = None
        self.sensor_clock_scale = 1.0
        self.sensor_sync_elapsed_ms = None
        self.sensor_sync_ros_ns = None

        self._stats = dict(
            dgram=0, frames=0, decoded=0, rejected=0,
            scans=0, sparse=0, timestamp_reset=0,
            clock_sync=0, imu_gate=0)
        self._last_fill = 0
        self.create_timer(1.0, self._report)

    def _report(self):
        stats = self._stats
        if stats['frames'] or stats['dgram'] or stats['scans']:
            self.get_logger().info(
                "lidar: %d dgram/s %d frames/s valid=%d rejected=%d "
                "scans=%d sparse_drop=%d timestamp_reset=%d "
                "clock_scale=%.4f sync=%d fill=%d/%d period=%.3fs "
                "imu_gate=%d"
                % (stats['dgram'], stats['frames'], stats['decoded'],
                   stats['rejected'], stats['scans'], stats['sparse'],
                   stats['timestamp_reset'], self.sensor_clock_scale,
                   stats['clock_sync'], self._last_fill, SCAN_BEAMS,
                   self.scan_period, stats['imu_gate']))
        else:
            self.get_logger().warn("lidar: NO UDP packets arriving on 5006")

        for key in stats:
            stats[key] = 0

    def _on_imu(self, _message):
        self.last_imu_ns = self.get_clock().now().nanoseconds

    def _imu_is_fresh(self):
        now_ns = self.get_clock().now().nanoseconds
        fresh = (
            self.last_imu_ns is not None
            and now_ns - self.last_imu_ns <= IMU_STALE_NS)
        if fresh == self.imu_gate_active:
            self.imu_gate_active = not fresh
            if fresh:
                self.get_logger().info(
                    "IMU data restored; publishing LiDAR scans")
            else:
                self.get_logger().error(
                    "IMU data stale; withholding LiDAR scans to protect SLAM")
        return fresh

    def _unwrap_sensor_timestamp(self, timestamp_ms):
        """Return (unwrapped_ms, reset) without assigning ROS time yet."""
        if self.sensor_last_ms is None:
            self.sensor_last_ms = timestamp_ms
            return 0, False

        delta = timestamp_ms - self.sensor_last_ms
        if delta < 0:
            if self.sensor_last_ms > 25000 and timestamp_ms < 5000:
                delta += SENSOR_TIMESTAMP_WRAP_MS
            elif delta > -100:
                # Ignore minor UDP reordering without moving time backward.
                return None, False
            else:
                # Reset timing state after a sensor reboot.
                self._stats['timestamp_reset'] += 1
                self.sensor_elapsed_ms = 0
                self.sensor_clock_scale = 1.0
                self.sensor_sync_elapsed_ms = None
                self.sensor_sync_ros_ns = None
                self.sensor_last_ms = timestamp_ms
                return 0, True

        self.sensor_elapsed_ms += delta
        self.sensor_last_ms = timestamp_ms
        return self.sensor_elapsed_ms, False

    def _map_sensor_batch_to_ros(self, timed_frames):
        """Anchor a receive batch to ROS while retaining calibrated spacing."""
        now_ns = self.get_clock().now().nanoseconds
        newest_elapsed_ms = timed_frames[-1][1]

        # Learn sensor-to-ROS clock scale while rejecting network-stall outliers.
        if self.sensor_sync_elapsed_ms is None:
            self.sensor_sync_elapsed_ms = newest_elapsed_ms
            self.sensor_sync_ros_ns = now_ns
        else:
            sensor_span_ms = newest_elapsed_ms - self.sensor_sync_elapsed_ms
            if sensor_span_ms >= 1000:
                ros_span_ns = now_ns - self.sensor_sync_ros_ns
                observed_scale = ros_span_ns / (sensor_span_ms * 1_000_000.0)
                if 0.8 <= observed_scale <= 1.2:
                    if self.sensor_clock_scale == 1.0:
                        self.sensor_clock_scale = observed_scale
                    else:
                        self.sensor_clock_scale = (
                            0.75 * self.sensor_clock_scale
                            + 0.25 * observed_scale)
                    self._stats['clock_sync'] += 1
                self.sensor_sync_elapsed_ms = newest_elapsed_ms
                self.sensor_sync_ros_ns = now_ns

        # Anchor the newest frame at receipt and back-date earlier batch frames.
        mapped = []
        for decoded, elapsed_ms in timed_frames:
            age_ns = int(
                (newest_elapsed_ms - elapsed_ms)
                * self.sensor_clock_scale * 1_000_000.0)
            mapped.append((decoded, now_ns - age_ns))

        self.sensor_last_ros_ns = now_ns
        return mapped

    def _start_new_rotation(self, boundary_stamp_ns, speed_deg_s):
        if 720.0 <= speed_deg_s <= 3600.0:
            self.rotation_speeds.append(speed_deg_s)
            if len(self.rotation_speeds) > 50:
                self.rotation_speeds.pop(0)
            self.scan_period = 360.0 / statistics.median(self.rotation_speeds)

        if self.scan_start_stamp_ns is not None:
            self.publish_scan()

        # Discard the initial partial rotation.
        self.current_scan = [math.nan] * SCAN_BEAMS
        self.scan_start_stamp_ns = boundary_stamp_ns

    def process_data(self):
        datagrams = self.receiver.get_available_packets()
        self._stats['dgram'] += len(datagrams)

        frames = []
        for datagram in datagrams:
            # Split batched datagrams and count incomplete trailing data.
            complete_size = len(datagram) - (len(datagram) % 47)
            for offset in range(0, complete_size, 47):
                frames.append(datagram[offset:offset + 47])
            if complete_size != len(datagram):
                self._stats['rejected'] += 1

        self._stats['frames'] += len(frames)

        timed_frames = []
        for frame in frames:
            decoded = decode_packet_with_metadata(frame)
            if decoded is None:
                self._stats['rejected'] += 1
                continue
            self._stats['decoded'] += 1

            elapsed_ms, reset = self._unwrap_sensor_timestamp(
                decoded.timestamp_ms)
            if elapsed_ms is None:
                self._stats['rejected'] += 1
                continue

            if reset:
                # Never assemble a scan across sensor clock epochs.
                self._stats['rejected'] += len(timed_frames)
                timed_frames = []
                self.current_scan = [math.nan] * SCAN_BEAMS
                self.scan_start_stamp_ns = None
                self.last_raw_start_angle = None

            timed_frames.append((decoded, elapsed_ms))

        if not timed_frames:
            return

        for decoded, frame_stamp_ns in self._map_sensor_batch_to_ros(
                timed_frames):

            raw_start = decoded.raw_start_angle
            wrapped = (
                self.last_raw_start_angle is not None
                and raw_start < 20.0
                and self.last_raw_start_angle > 340.0
            )
            if wrapped:
                self._start_new_rotation(frame_stamp_ns, decoded.speed_deg_s)

            self.last_raw_start_angle = raw_start

            # rf2o expects a ROS-oriented scan centered at zero radians.
            for angle_degrees, distance in decoded.points:
                index = int(round((angle_degrees + 180.0) % 360.0)) % SCAN_BEAMS
                current = self.current_scan[index]
                if not math.isfinite(current) or distance < current:
                    self.current_scan[index] = distance

    def publish_scan(self):
        valid_count = sum(math.isfinite(value) for value in self.current_scan)
        self._last_fill = valid_count
        if valid_count < MIN_SCAN_FILL:
            self._stats['sparse'] += 1
            return

        if not self._imu_is_fresh():
            self._stats['imu_gate'] += 1
            return

        message = LaserScan()
        message.header.stamp.sec = self.scan_start_stamp_ns // 1_000_000_000
        message.header.stamp.nanosec = self.scan_start_stamp_ns % 1_000_000_000
        message.header.frame_id = 'base_laser'

        message.angle_min = -math.pi
        message.angle_increment = (2.0 * math.pi) / SCAN_BEAMS
        message.angle_max = (
            message.angle_min + (SCAN_BEAMS - 1) * message.angle_increment)

        message.scan_time = self.scan_period
        message.time_increment = message.scan_time / SCAN_BEAMS
        message.range_min = 0.1
        message.range_max = 8.0
        message.ranges = self.current_scan
        message.intensities = [0.0] * SCAN_BEAMS

        self.publisher_.publish(message)
        self._stats['scans'] += 1
