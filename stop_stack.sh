#!/bin/bash
# Stop the complete ROS2 process tree, including orphaned launch children.
PAT='ros2 launch launch_all|imu_bridge|python3 .*/root/code/main\.py|rviz2|slam_toolbox|ekf_node|rf2o_laser|static_transform_publisher'

pkill -f "$PAT" >/dev/null 2>&1

for i in $(seq 1 20); do
  LEFT=$(pgrep -f "$PAT" | wc -l)
  if [ "$LEFT" -eq 0 ]; then break; fi
  sleep 0.5
done

# Force-stop anything that ignored SIGTERM.
pkill -9 -f "$PAT" >/dev/null 2>&1
sleep 1

REMAIN=$(pgrep -f "$PAT" | wc -l)
echo "stopped; remaining processes: $REMAIN"
if [ "$REMAIN" -ne 0 ]; then
  echo "WARNING: could not kill:"
  pgrep -af "$PAT"
  exit 1
fi
exit 0
