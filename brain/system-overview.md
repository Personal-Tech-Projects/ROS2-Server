# System overview

**Last verified against actual code:** 2026-08-01, Session 3 (re-read every
file below directly instead of relying on memory — treat this as the current
source of truth, superseding earlier drafts of this file).

## The two "servers" the ESP32 talks to

The ESP32 firmware (Arduino, `server_ip = "192.168.4.81"`) talks to two
logically separate things:

1. **"Robot server" / `my-robot-server`** — handles motor control / manual
   override. Answers a plain TCP handshake on port **5005**. **Not part of
   this repo (`/root/code`)** — its actual source has never been shown to us.
   There IS a container named `my-robot-server` visible in `docker ps -a` on
   this Windows machine's Docker Desktop (`blavigne0/robot-dev:v1`, an
   interactive `/bin/bash` session with no restart policy), but **the user
   has since clarified the real robot-server runs on a separate VM** — so
   that local container may be an unrelated/stale leftover, not the thing
   actually answering port 5005 in normal operation. **Unresolved** — see
   `open-questions.md`.
2. **"ROS2 server" / `robot_brain` container** — this is the container we
   work inside. Runs `ros2 launch launch_all.py` from `/root/code`.

## Full sensor data pipeline (confirmed by reading every file, Session 3)

```
BNO085 IMU  --I2C-->  ESP32 --UDP:8888--> imu_bridge.py --/imu/data (Imu)--------\
                                                                                   >-- ekf_filter_node --/odometry/filtered + odom->base_link TF--> slam_toolbox --> map
LD14P LiDAR --UART--> ESP32 --UDP:5006--> lidar_node.py --/scan (LaserScan)--+---/
                                                                              \-- rf2o_laser_odometry --/odom_rf2o (Odometry)--/
                                                                              \-- (also read directly by slam_toolbox for scan matching)
```

### IMU side
- `imu_bridge.py`: node `imu_bridge`, binds UDP `0.0.0.0:8888`, background
  thread parses ASCII `ROT,w,x,y,z` (orientation quaternion) and
  `ACC,x,y,z` (linear acceleration) lines, publishes `sensor_msgs/Imu` on
  `imu/data` (-> `/imu/data`, no namespace). Manually sets covariance
  diagonals to `0.01` on orientation/angular_velocity/linear_acceleration —
  **required**, since the EKF ignores any field with 0 covariance.

### LiDAR side
- `main.py` just instantiates `LidarBridgeNode` (from `lidar_node.py`) and spins it.
- `lidar_node.py`: node `lidar_bridge`, owns a `LidarUdpReceiver` (in
  `udp_receiver.py`, listens on UDP 5006), polls it every 10ms via a ROS2
  timer. Each raw 47-byte packet goes through `ld14p_decoder.py`'s
  `decode_packet()`, which validates the header bytes (`0x54, 0x2C`) and
  unpacks 12 `(angle, distance)` points per packet using the LD14P's binary
  layout. Points accumulate into a 360-slot `current_scan` array (1 slot per
  degree); once the angle wraps past 360 back to <90, a full
  `sensor_msgs/LaserScan` is published on topic `scan` (-> `/scan`).

### Fusion — `ekf.yaml` (`robot_localization` `ekf_node`, node name `ekf_filter_node`)
Two-dimensional mode (`two_d_mode: true`), 30Hz. Config arrays are masks over
`[x,y,z, roll,pitch,yaw, vx,vy,vz, vroll,vpitch,vyaw, ax,ay,az]`:
- `odom0: /odom_rf2o`, uses **x, y, vx, vy** — position + linear velocity,
  entirely from LiDAR scan-matching (rf2o). This is the **only position
  source** in the whole system (no wheel encoders — motors are open-loop PWM).
- `imu0: /imu/data`, uses **yaw, ax, ay** only — explicitly ignores IMU
  roll/pitch/z (makes sense for a 2D ground robot) and does NOT use IMU
  angular velocity (BNO085 firmware only enables `SH2_ROTATION_VECTOR` and
  `SH2_LINEAR_ACCELERATION` reports, not gyro).
- Publishes fused output on `/odometry/filtered` and, since `publish_tf:
  true` and `world_frame: odom`, publishes the `odom -> base_link` TF.

**Confirmed live in Session 2:** `/odometry/filtered`'s orientation tracks
`/imu/data`'s raw orientation closely — proof the IMU is actually being
fused, not just ignored.

### Static transforms (needed so EKF/SLAM know sensor placement)
Both currently identity (0 offset, 0 rotation) — sensors are treated as
coincident with the robot's center:
- `imu_static_tf` (`base_link -> imu_link`), declared in `launch_all.py`
- `base_to_laser_broadcaster` (`base_link -> base_laser`), declared in
  `launch_odometry.py`

**User has flagged (2026-08-01) that these being declared in two different
files is annoying to maintain — see `todo.md` for the planned consolidation.**

### Mapping — `launch_slam.py` (`slam_toolbox`, `async_slam_toolbox_node`)
Subscribes directly to `/scan` (raw LiDAR) for scan matching, and reads the
`odom -> base_link` TF (published by the EKF) from the TF tree as its motion
prior between scans. Publishes `map -> odom` TF, completing
`map -> odom -> base_link -> {imu_link, base_laser}`, and produces the actual
occupancy grid map.

**IMU's contribution to the map is indirect**: slam_toolbox never reads
`/imu/data` directly. The IMU improves the *yaw* component of the
`odom -> base_link` TF that the EKF hands to slam_toolbox, which gives
scan-matching a better starting guess each cycle — this mainly helps
rotation accuracy, since rf2o's laser-only odometry can drift on yaw in
sparse/feature-poor environments.

### Do we still need rf2o, now that the IMU is confirmed feeding the EKF?
**Yes — rf2o is not redundant with the IMU, and should not be removed.**

**Correction to an earlier, overstated version of this note:** the IMU's
`ax`/`ay` ARE fed into the EKF's internal motion model (per the `imu0_config`
mask above) — the EKF's prediction step does propagate position/velocity
from that acceleration between updates, at the EKF's own rate (30Hz), faster/
lower-latency than rf2o's scan-matching updates (20Hz). So it's wrong to say
the IMU contributes nothing to position — it does, as high-rate/low-latency
*propagation*.

This is actually the same architecture used in real lidar-inertial SLAM
systems like **LIO-SAM** (arxiv 2007.00258): IMU (pre-)integration provides
fast motion propagation between LiDAR scans, and a **lidar odometry factor
corrects that IMU-driven estimate** (and even estimates IMU bias) each scan.
`rf2o` — frame-to-frame LiDAR scan matching producing incremental motion —
*is* that lidar-odometry correction step, just via a simpler loosely-coupled
EKF here instead of LIO-SAM's tightly-coupled factor graph. In other words:
rf2o isn't a redundant extra layer on top of "IMU does odometry, lidar
corrects drift" — it IS the "lidar corrects drift" half of exactly that
design. The reason it can't be dropped: raw double-integrated IMU
acceleration always drifts (this is true even in LIO-SAM — the whole reason
it continuously re-estimates IMU bias against the lidar factor); rf2o is the
only signal in this system that ever tells the filter "here's an actual
measured displacement," and there's no other candidate (no GPS, no wheel
encoders). Removing rf2o removes the correction half of the very
architecture being proposed, not some separate redundant piece.

Additional real-world nuance found when researching this (see
`investigation-log.md` for the session this was checked in): some
lidar-inertial implementations deliberately avoid feeding raw linear
acceleration into the filter at all, because MEMS accelerometer readings are
noisy and sensitive to vibration/mounting — arguably a reason to weight
rf2o's LiDAR-based motion estimate *more* heavily than the IMU's
acceleration, not less.

**Follow-up: doesn't `slam_toolbox` already correct drift itself, since it
also subscribes to `/scan` directly? Then why keep rf2o at all?** Because
`rf2o` and `slam_toolbox`'s own scan matching operate at different levels,
not the same one:

- **`rf2o` = scan-to-scan (frame-to-frame) matching.** Compares each new
  scan only to the immediately previous scan. No memory of the map at all.
  Fast/cheap, but its own errors accumulate scan after scan (same failure
  mode as wheel-odometry drift, just laser-derived).
- **`slam_toolbox` = scan-to-map matching + loop closure.** Matches the
  current scan against the whole accumulated map (not just the last scan),
  and can recognize previously-visited areas to retroactively correct the
  whole trajectory via pose-graph optimization. Fundamentally stronger
  correction than scan-to-scan.

Crucially, `slam_toolbox`'s own scan matcher needs the `odom -> base_link`
transform as an **initial guess / search seed** for where to even start
matching against that big map — without a decent seed, matching a large or
repetitive/symmetric map is a much harder search problem (slower
convergence, risk of converging to the wrong spot entirely, especially with
larger motion between scans). Confirmed this isn't just an assumption:
there's an open slam_toolbox feature request,
["Support mapping without odometry" (#221)](https://github.com/SteveMacenski/slam_toolbox/issues/221)
— i.e. odometry-free operation is explicitly NOT the standard supported
mode. (Hector SLAM is the well-known exception that genuinely runs without
external odometry — but only because it runs its own internal fast
scan-to-scan matcher as a substitute, which is functionally the same
computation `rf2o` already provides here, just built into the mapper
instead of being a separate node.)

**So the real picture is 3 layers of correction, not 2:**
```
IMU (fast, ax/ay/yaw) --propagates--> EKF
rf2o (scan-to-scan)   --corrects-->   EKF --publishes odom->base_link (seed)--> slam_toolbox
/scan (raw)           ------------------------------------------------------->  slam_toolbox (scan-to-map + loop closure)
```
Each layer bounds the drift of the layer before it, at a coarser but more
globally-referenced level: IMU alone drifts fastest → rf2o bounds that
locally, scan-to-scan → slam_toolbox bounds *rf2o's own* accumulated drift
globally, scan-to-map. Removing rf2o would not let slam_toolbox "just do the
job instead" — it would remove the seed slam_toolbox's own matcher depends
on to search efficiently and correctly in the first place.

**Actual rates of each layer (checked 2026-08-01):** unlike IMU/rf2o, the
scan-to-map layer is NOT a fixed timer — it's gated by both LiDAR input rate
and motion:
| Layer | Trigger | Effective rate |
|---|---|---|
| IMU -> EKF | fixed timer | 30Hz (`ekf.yaml` `frequency`) |
| rf2o -> EKF | fixed timer | 20Hz (`launch_odometry.py` `freq` param) |
| slam_toolbox scan-to-map | new `/scan` **and** >=0.5m or 0.5rad moved since last processed scan | <=6Hz input cap, often much less |

- **Input cap:** the LD14P LiDAR's datasheet default is **6Hz** (adjustable
  2-8Hz range via the sensor's own serial config protocol). **Confirmed
  (2026-08-01) our code never touches this** — re-read `lidar_node.py`,
  `ld14p_decoder.py`, `udp_receiver.py`, and the ESP32 `.ino`: the ESP32 only
  ever *reads* from `Serial2`, it never writes a config/speed command to the
  LiDAR. So this number is a **documented default, not a live-verified
  rate** — if this specific unit was ever reconfigured by someone/something
  outside this repo, it could be running at a different speed and nothing
  here would know. **Not yet measured empirically** — to actually confirm,
  next time the ESP32 + stack are running, diff consecutive `/scan` message
  header timestamps (don't use `ros2 topic hz`, see the false-negative
  gotcha in `commands.md`).
- `lidar_node.py` publishes one `LaserScan` per
  full rotation — so at most ~6 scans/sec ever reach `slam_toolbox`, well
  below the EKF's 30/20Hz.
- **Processing throttle:** `slam_toolbox`'s defaults (from its own
  `mapper_params_online_async.yaml`, not overridden by our
  `launch_slam.py`) are `minimum_travel_distance: 0.5` (meters) and
  `minimum_travel_heading: 0.5` (radians, ~29 deg) — it skips processing a
  new scan into the map until the robot has moved that much since the last
  one it actually processed. Sitting still or moving slowly = long gaps
  between real corrections, even though scans keep arriving at ~6Hz.
- Separately, `map_update_interval: 5.0`s (also an unoverridden default)
  controls how often the *visible* occupancy grid is regenerated for
  RViz/other consumers — unrelated to the internal pose-graph correction
  itself.

**Historical context (per user, 2026-08-01):** rf2o was the original design,
added *because* the robot had no IMU yet and `slam_toolbox` (in the
`async_slam_toolbox_node` mode used here) requires some form of odometry
input alongside the LiDAR scans. The IMU was bought and integrated later, but
it was never meant to (and structurally can't) replace that odometry role —
it only adds yaw/acceleration. The one way to actually drop rf2o would be
switching `slam_toolbox` to an odometry-free scan-matching mode, which is a
materially different, generally less robust setup — not a natural consequence
of having added the IMU. Not planned; would need a deliberate decision if
ever revisited.

## Historical: the TCP "gate" bug (RESOLVED, Session 2 — kept for context)

The ESP32 firmware used to have a hard gate at the top of `loop()`: if the
TCP:5005 handshake to the robot-server hadn't succeeded, it hit a `return;`
that skipped IMU read/send, LiDAR read/send, and everything else — meaning
**a down robot-server silently killed ALL sensor data too**, not just motor
control. This was the actual root cause of the original "sometimes have to
restart something to get the connection working" symptom (see
`investigation-log.md` Session 2 for the full trace). **Fixed**: `loop()` was
rewritten so the handshake retries in the background (rate-limited to once
per 2s) without blocking sensor code; only the manual-override UDP listener
still correctly waits for a successful handshake. Verified via
`esp32-deployment.md`'s deploy-verification procedure and a 5/5-cycle
resilience stress test (container restarts + ESP32 resets, see
`investigation-log.md`).

**Correction (Session 3):** earlier drafts of this file guessed the
robot-server was answering the ESP32's original TCP:5005 handshake was likely
a removed micro-ROS agent, and separately floated a WSL2/Docker-NAT
port-forwarding theory as the "leading hypothesis" for the flaky-connection
symptom. Neither of those explains what was actually observed — the real
cause was the firmware's all-or-nothing TCP gate combined with
`my-robot-server` (whatever/wherever it actually is) not staying up. The
WSL2-NAT theory was investigated at reasonable depth in Session 2 and never
actually confirmed with a live capture; treat it as a **deprioritized,
unproven side theory**, not the explanation for this symptom.

## A DIFFERENT, separately-confirmed port-forwarding failure (Session 4, 2026-08-01)

Important: this is a **different incident** from the TCP-gate bug above —
don't conflate them. Symptom: both containers freshly started, ROS2 stack
launched, but RViz showed no map. Debugging found `/imu/data` fully healthy
but `/scan` never publishing, and `rf2o_laser_odometry` continuously logging
`"Waiting for laser_scans...."`.

**This time, the WSL2/Docker port-forwarding failure theory was directly
proven, not just inferred** — for UDP port 5006 (LiDAR) specifically, with
port 8888 (IMU) on the exact same container working fine simultaneously:

1. `Get-NetUDPEndpoint` (Windows host, PowerShell) showed a real listening
   socket for 8888 but **none at all for 5006**, even though `docker port
   robot_brain` claimed both were published. This proves Windows has no
   "catcher" bound for 5006 to hand packets into WSL2/the container.
2. To confirm packets were still arriving at the Windows NIC despite that
   (i.e. rule out "ESP32 isn't sending" before blaming forwarding) — ran
   `pktmon` (Windows built-in packet capture, needs an elevated/Administrator
   terminal) filtered to port 5006:
   ```powershell
   pktmon filter remove
   pktmon filter add -p 5006
   pktmon start --etw -f "$env:TEMP\pktmon_5006.etl" --capture
   # ...wait ~15s while ESP32 is sending...
   pktmon stop
   pktmon format "$env:TEMP\pktmon_5006.etl" -o "$env:TEMP\pktmon_5006.txt"
   ```
   The formatted trace showed **continuous real packets throughout the whole
   capture window**: `192.168.4.78.4210 > 192.168.4.81.5006: UDP, length 47`
   — the ESP32's actual IP, the right port, and 47 bytes (`PACKET_SIZE` in
   the firmware) — proving the ESP32 was sending correctly and the packets
   were physically reaching the Windows PC's network card.

**Conclusion: packets arrive at Windows, then get silently dropped because
nothing is listening on 5006 to catch them** — never reaching WSL2, Docker,
or `lidar_node.py`'s socket. Not an ESP32 problem, not a LiDAR hardware
problem. This is a real, reproducible instance of the port-forwarding-goes-
stale failure mode that was only ever theorized (and left unproven) in
Session 2 — just isolated to a single port this time instead of everything.
**Fix:** same as established before — restart the `robot_brain` container to
force Docker to recreate its port publishing (requires relaunching
`ros2 launch launch_all.py` afterward, no auto-start, and reloading any
connected VS Code window). Outcome of that fix attempt: see next
`investigation-log.md` entry.

**Reusable diagnostic recipe** for "container claims a port is published but
data isn't arriving": check `docker port <container>` (what Docker thinks)
against `Get-NetUDPEndpoint` (what Windows actually has bound) for a
mismatch; if the port that's missing is suspect, use `pktmon` to prove
whether packets are reaching the NIC at all before concluding anything about
the sender.

## Repo map (`/root/code`)

Full top-level Python file list (confirmed Session 3):
- `imu_bridge.py` — UDP(8888) -> `/imu/data` bridge
- `main.py` — thin entry point, spins `LidarBridgeNode`
- `lidar_node.py` — UDP(5006) -> `/scan` bridge (the actual node class)
- `ld14p_decoder.py` — binary LD14P packet -> `(angle, distance)` decoder
- `udp_receiver.py` — the `LidarUdpReceiver` socket wrapper `lidar_node.py` uses
- `ekf.yaml` — `robot_localization` ekf_node config
- `launch_all.py` — top-level launch file (what the user runs)
- `launch_odometry.py` — static TF (laser) + rf2o + ekf_node
- `launch_slam.py` — slam_toolbox
- `udp_tester.py`, `map_extractor.py` — present in the repo, not yet read/
  documented — purpose unconfirmed, follow up if relevant
- `/root/microros_ws` — vendored micro-ROS agent source, NOT currently
  launched by anything in this repo (removed in commit `125abfd`)
