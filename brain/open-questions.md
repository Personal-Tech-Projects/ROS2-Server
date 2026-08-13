# Open questions — need the user to confirm

- Where does the "robot server" (non-SLAM, answers TCP:5005, handles motor
  commands) actually run, and what implements the TCP:5005 handshake? Not in
  `/root/code` or `/root/microros_ws`. Only matters if LiDAR ever stops working
  too (right now it's presumably fine since LiDAR data gets through).
- Is the ESP32 physically powered on / connected to WiFi *right now*, for
  today's test run? (LiDAR-not-run-yet-today context from earlier in the
  session — need to confirm the robot is actually live before concluding
  anything from a lack of topic data.)
- Confirm exact repro steps for "sometimes have to restart the docker
  container to get the connection to work" — does this happen after a crash,
  after leaving it running overnight, after code changes, or seemingly
  randomly? This matters for testing the stale-UDP-socket hypothesis in
  `system-overview.md`.
- **IMU magnetometer interference / calibration (raised 2026-08-01):** the
  BNO085 was mounted at the robot's center with no calibration performed.
  Firmware uses `SH2_ROTATION_VECTOR` (9-axis, magnetometer-fused absolute
  heading) — the magnetometer is the one component that isn't fully
  automatic (benefits from a figure-8 calibration motion) and is vulnerable
  to magnetic interference from nearby motors/battery/WiFi, all of which sit
  close to the IMU on this robot. Unconfirmed whether this is actually
  causing yaw bias/noise in practice — no live comparison done yet. Two
  options if it turns out to matter: (a) do the figure-8 calibration
  routine, or (b) switch firmware to `SH2_GAME_ROTATION_VECTOR` (accel+gyro
  only, immune to magnetic interference, trades off slow yaw drift — which
  `rf2o`/`slam_toolbox` already correct for elsewhere in this pipeline, per
  `system-overview.md`'s 3-layer correction section). Not yet decided or
  tested — needs a live calibration-quality check first (BNO08x calibration
  status reports) before picking an option.
- **IMU mounting orientation not verified (raised 2026-08-01):** the current
  static transform (`imu_static_tf`) assumes the IMU's physical X/Y/Z axes
  are perfectly aligned with the robot's `base_link` forward/left/up axes
  (identity rotation, no offset). This was never empirically checked against
  the actual physical mounting. See `todo.md` for the verification steps.
