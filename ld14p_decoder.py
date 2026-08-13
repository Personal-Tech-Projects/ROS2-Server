"""Decode and validate native 47-byte LD14P measurement frames."""

from dataclasses import dataclass
import math
import struct


PACKET_SIZE = 47
POINTS_PER_PACKET = 12

# LD14/LD14P ranging-center calibration constants from LDROBOT's driver.
RANGING_CENTER_X_MM = 5.9
RANGING_CENTER_Y_OFFSET_MM = -18.975571
RANGING_CENTER_Y_SCALE = 0.11923


@dataclass(frozen=True)
class DecodedPacket:
    points: list
    raw_start_angle: float
    speed_deg_s: float
    timestamp_ms: int


def crc8(data):
    """LDROBOT CRC-8 (polynomial 0x4D, initial value 0)."""
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            if value & 0x80:
                value = ((value << 1) ^ 0x4D) & 0xFF
            else:
                value = (value << 1) & 0xFF
    return value


def _to_ros_angle(raw_clockwise_angle, distance_mm):
    """Convert LD14P clockwise angle to calibrated ROS counterclockwise angle."""
    x = distance_mm + RANGING_CENTER_X_MM
    y = distance_mm * RANGING_CENTER_Y_SCALE + RANGING_CENTER_Y_OFFSET_MM
    center_shift = math.degrees(math.atan2(y, x))

    # Apply calibration before converting clockwise hardware angles to ROS.
    return (-raw_clockwise_angle + center_shift) % 360.0


def decode_packet_with_metadata(data):
    if len(data) != PACKET_SIZE or data[0] != 0x54 or data[1] != 0x2C:
        return None

    if crc8(data[:-1]) != data[-1]:
        return None

    speed_deg_s = float(struct.unpack_from('<H', data, 2)[0])
    raw_start_angle = struct.unpack_from('<H', data, 4)[0] * 0.01
    raw_end_angle = struct.unpack_from('<H', data, 42)[0] * 0.01
    timestamp_ms = struct.unpack_from('<H', data, 44)[0]

    if not 360.0 <= speed_deg_s <= 3600.0:
        return None

    angular_span = (raw_end_angle - raw_start_angle) % 360.0
    # Reject impossible spans; CRC remains the primary integrity check.
    if angular_span > 30.0:
        return None

    angle_step = angular_span / (POINTS_PER_PACKET - 1)
    points = []

    for index in range(POINTS_PER_PACKET):
        data_index = 6 + index * 3
        distance_mm = struct.unpack_from('<H', data, data_index)[0]
        if not 100 < distance_mm < 8000:
            continue

        raw_angle = (raw_start_angle + index * angle_step) % 360.0
        ros_angle = _to_ros_angle(raw_angle, distance_mm)
        points.append((ros_angle, distance_mm / 1000.0))

    return DecodedPacket(
        points=points,
        raw_start_angle=raw_start_angle,
        speed_deg_s=speed_deg_s,
        timestamp_ms=timestamp_ms,
    )


def decode_packet(data):
    """Backward-compatible points-only decoder used by older diagnostics."""
    decoded = decode_packet_with_metadata(data)
    return decoded.points if decoded else []
