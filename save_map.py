#!/usr/bin/env python3
"""Save localization and occupancy-map files from SLAM Toolbox."""

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

import rclpy
from rclpy.node import Node
from slam_toolbox.srv import SaveMap, SerializePoseGraph


MAP_FILES = (".posegraph", ".data", ".yaml", ".pgm")
VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class MapSaver(Node):
    def __init__(self):
        super().__init__("robot_map_saver")
        self.serialize = self.create_client(
            SerializePoseGraph, "/slam_toolbox/serialize_map"
        )
        self.save_occupancy = self.create_client(
            SaveMap, "/slam_toolbox/save_map"
        )

    def call(self, client, request, service_name, timeout=30.0):
        if not client.wait_for_service(timeout_sec=10.0):
            raise RuntimeError(f"{service_name} is not available")

        future = client.call_async(request)
        rclpy.spin_until_future_complete(self, future, timeout_sec=timeout)
        if not future.done():
            raise RuntimeError(f"{service_name} timed out")

        response = future.result()
        if response is None or response.result != 0:
            result = "no response" if response is None else response.result
            raise RuntimeError(f"{service_name} failed with result {result}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Save a SLAM Toolbox map for localization and Nav2"
    )
    parser.add_argument("name", help="Versioned map name, for example home_v1")
    parser.add_argument(
        "--directory", default="/root/robot-maps", help=argparse.SUPPRESS
    )
    return parser.parse_args()


def validate_destination(directory, name):
    if not VALID_NAME.fullmatch(name):
        raise ValueError(
            "Map names may contain only letters, numbers, underscores, and hyphens"
        )

    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        raise ValueError(f"Map directory does not exist: {directory}")

    destinations = [directory / f"{name}{suffix}" for suffix in MAP_FILES]
    existing = [path.name for path in destinations if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to overwrite existing map files: " + ", ".join(existing)
        )
    return directory, destinations


def main():
    args = parse_args()
    try:
        directory, destinations = validate_destination(args.directory, args.name)
    except (ValueError, FileExistsError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    staging = Path(tempfile.mkdtemp(prefix=".saving-", dir=directory))
    staged_base = staging / args.name
    rclpy.init()
    saver = MapSaver()

    try:
        serialize_request = SerializePoseGraph.Request()
        serialize_request.filename = str(staged_base)
        saver.call(
            saver.serialize,
            serialize_request,
            "/slam_toolbox/serialize_map",
        )

        occupancy_request = SaveMap.Request()
        occupancy_request.name.data = str(staged_base)
        saver.call(
            saver.save_occupancy,
            occupancy_request,
            "/slam_toolbox/save_map",
        )

        staged_files = [staged_base.with_suffix(suffix) for suffix in MAP_FILES]
        missing = [path.name for path in staged_files if not path.is_file()]
        empty = [path.name for path in staged_files if path.is_file() and path.stat().st_size == 0]
        if missing or empty:
            details = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if empty:
                details.append("empty: " + ", ".join(empty))
            raise RuntimeError("Incomplete map output (" + "; ".join(details) + ")")

        for source, destination in zip(staged_files, destinations):
            source.replace(destination)

        print(f"Saved map '{args.name}' to {directory}")
        for path in destinations:
            print(f"  {path.name}: {path.stat().st_size} bytes")
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        saver.destroy_node()
        rclpy.shutdown()
        shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
