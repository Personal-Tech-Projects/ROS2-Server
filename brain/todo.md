# To-do — code cleanup / refactor tasks

Actionable tasks the user wants done, not unresolved investigation questions
(see `open-questions.md` for those). Check items off by editing this file
directly when done; keep finished items with a `[x]` and a short note rather
than deleting them, so we keep a record of what's already been cleaned up.

- [ ] **Consolidate all static transforms into one place.** Requested
  2026-08-01. Right now static transform publishers are declared in two
  different files:
  - `imu_static_tf` (IMU -> base_link, all zeros) in `launch_all.py`
  - `base_to_laser_broadcaster` (LiDAR -> base_link, all zeros) in
    `launch_odometry.py`

  User wants these organized in a single, predictable location instead of
  having to search across launch files to find/add a static transform.

  **Recommended approach (agreed 2026-08-01):** move both
  `Node(...static_transform_publisher...)` entries into one file — either
  `launch_all.py` itself or a new dedicated `launch_transforms.py` included
  from `launch_all.py` — so `launch_odometry.py` only contains the rf2o + EKF
  nodes. Chosen over switching to a URDF/`robot_state_publisher` setup (the
  more "standard" ROS2 way to declare sensor offsets) because both current
  transforms are zero-offset placeholders — URDF is worth it once there are
  real physical offsets to model or more sensors are added, not before.
  - Next step: implement the file move, not yet done.

- [ ] **Verify IMU mounting orientation matches the code's assumption.**
  Raised 2026-08-01. `imu_static_tf` (`base_link -> imu_link`) currently
  assumes zero rotation offset — i.e. the BNO085's physical axes line up
  exactly with the robot's forward/left/up. Never empirically checked. See
  related open question in `open-questions.md` about magnetometer
  calibration/interference too.

  **Verification steps (agreed 2026-08-01):**
  1. Power on the full stack, watch `/imu/data` live
     (`ros2 topic echo /imu/data --once`, repeated — not `topic hz`).
  2. Push the robot straight forward by hand; note which axis of
     `linear_acceleration` spikes and its sign. Should be positive `x` if
     mounted as the code assumes.
  3. Rotate the robot counter-clockwise (viewed from above) by hand; the
     orientation quaternion's yaw should increase. If it decreases or the
     wrong axis moves, the IMU is rotated relative to what the code assumes.
  4. If mismatched: either physically re-mount the IMU to match, or update
     `imu_static_tf`'s rotation arguments (currently `0.0, 0.0, 0.0`) to
     encode the real offset instead of re-mounting hardware.
  - Do this **before** the static-transform-consolidation task above, so the
    correct values (if any offset is found) carry into the new file instead
    of copying the unverified `0,0,0` forward.
  - Not yet done — needs the robot physically available and powered on.
