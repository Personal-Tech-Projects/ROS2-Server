# Investigation log (newest entry on top)

## 2026-08-12: Localization map persistence and saving

- Created `C:\Users\jjlav\robot-maps` and mounted it read/write at
  `/root/robot-maps` in `robot_brain`.
- Preserved the pre-existing map artifacts under `robot-maps/legacy`; they are
  not validated localization maps.
- Added `save_map.py` and `tools/save-map.ps1`. The command calls SLAM
  Toolbox's `serialize_map` and `save_map` services, validates all four output
  files, and refuses to overwrite an existing version.
- Verified a temporary save produced valid `.posegraph`, `.data`, `.yaml`, and
  `.pgm` files visible on Windows. The overwrite test failed safely, and the
  temporary outputs were removed.
- During the test, IMU remained at 48 packets/s with no decode failures and
  LiDAR remained at 5-6 scans/s with no rejected frames.
- RViz was restored without restarting ROS using a direct Docker-to-WSLg X11
  bridge because the VS Code X11 proxy became stale after container restart.

To save a validated map:

```powershell
powershell.exe -ExecutionPolicy Bypass -File C:\Users\jjlav\robot-tools\save-map.ps1 home_v1
```

## 2026-08-05 — Session 5 (continued): four more root causes found, all measured

Everything below was measured at both ends, not inferred. Several earlier
theories in this log were wrong and are corrected here.

### A. `/scan` was crippled by the bridge's own logging (FIXED)

`lidar_node.py` called `get_logger().info()` **once per received packet** (plus
once per timer cycle). At the LiDAR's ~258 packets/sec those logger calls cost
far more than decoding the packets. The 10 ms timer fell behind, the kernel
receive buffer overflowed (**10,787 drops** counted in `/proc/net/udp`), dropped
packets meant missed 360-degree wraps, and `/scan` collapsed.

| | before | after |
|---|---|---|
| `/scan` rate | 1.1 Hz | 5.7 Hz |
| fill | 58% (209/360) | 76% |
| new kernel drops | accumulating | 0 |

There is **no scan-rate setting** anywhere in this system. The rate is emergent:
the LD14P spins at a fixed rate and `lidar_node` publishes one scan per
completed rotation. 1.1 Hz was the *symptom* of missing most rotations.

Fixed by summarising once per second instead. `imu_bridge.py` had the same
defect (a `print()` per packet at ~100/s) and got the same treatment.

**Do not reintroduce per-packet logging in either bridge.**

### B. `imu_bridge.py` could die silently on one corrupt packet (FIXED)

`data.decode('utf-8')` sat **outside** the `try/except`. A single corrupted
datagram raises `UnicodeDecodeError`, kills the listener thread, and leaves the
node alive with a registered publisher and zero data — forever, with no error.
Now caught and counted. Both bridges also log `NO UDP packets arriving on <port>`
once per second when starved, which turns a silent failure into a visible one.

### C. 38% of packets never left the ESP32 (FIXED by batching)

The big one. Measured simultaneously at both ends over the same 32 s window:

| stage | packets | loss |
|---|---|---|
| frames read off `Serial2` | 8,092 (252/s) | — |
| `Udp.endPacket()` **succeeded** | 5,003 (155.8/s) | **38.2% lost here** |
| arrived at container | 5,043 (157.6/s) | ~0% |

**The network was lossless.** WiFi, the Windows host and Docker's forwarding
delivered essentially everything the ESP32 managed to send. All the loss was
`endPacket()` returning failure — the WiFi stack's transmit buffers could not
absorb 252 separate transmissions per second, so the packets never left the
board. The firmware never checked the return value, so this was invisible.

**Fix: batch LiDAR frames into one datagram** (`LIDAR_BATCH` in the sketch).

| | unbatched | batch 5 | batch 10 (battery) |
|---|---|---|---|
| LiDAR TX failures | 38.2% | 1.8% | **0%** |
| IMU TX failures | 30.4% | — | **0%** |
| scan fill | ~140/360 | 269/360 | **352/360 (98%)** |

Note the IMU improved **without being touched** — the LiDAR flood was starving
the shared WiFi transmit queue.

**RSSI is not the driver.** During the batch-5 measurement RSSI was *worse*
(-72 vs -67) and loss still fell 21x. Do not chase signal strength for this.

**Battery changes the load.** On the power bank the LiDAR spins ~2x faster
(516 frames/s vs 252 — the LD14P motor speed appears to track supply voltage),
which doubled the datagram rate and pushed failures back to 53%. Batch 10 fixed
it. **If the spin rate changes again, revisit `LIDAR_BATCH`.**

### D. The BNO085 stops reporting, and the firmware never noticed (FIXED)

The IMU went silent repeatedly — `isent` freezes on the ESP32 while the LiDAR
keeps streaming, so it is a sensor-side fault, not network. Seen at boot as
`I2C address not found / BNO08x not detected`, and mid-run with no message at
all. The original firmware called `begin_I2C()`/`enableReport()` once in
`setup()` and never checked again, so one hiccup killed heading until a power
cycle.

A watchdog now runs every loop: `bno08x.wasReset()` re-enables reports when the
sensor resets itself (it comes back with all reports DISABLED), plus a 3-second
fallback that rebuilds the I2C link. Rate-limited to 5 s so a dead sensor cannot
spin the loop.

This matters more than it used to: the `ekf.yaml` change made the IMU the
**sole** source of heading, so an IMU dropout now leaves yaw completely
unobserved and the map fragments.

### E. TWO STACKS AT ONCE — the worst failure mode of the day

Running `robot-up.ps1` while a stack was already running produced two complete
ROS2 stacks. Symptoms: axes jumping violently in RViz, and "old maps spread out"
across a map that ballooned to 153 x 88 m.

The jump watcher made the cause unambiguous — 8,139 discontinuities in 12,651
pose messages (**64%**), with this signature:

```
t=101.3s  YAW jump: dyaw=25.6 deg  dpos=80.137 m  in 0.019s
t=101.3s  YAW jump: dyaw=25.6 deg  dpos=80.137 m  in 0.014s
```

- `dpos=80.137 m` **identical every time** = the pose alternating between two
  fixed points, not drifting
- alternating 0.019 s / 0.015 s intervals = two independent 25 Hz publishers
- every event tagged "IMU steady" = the sensor was innocent

Two `ekf_node`s fight over `odom->base_link`; two `slam_toolbox`s publish `/map`
with independent origins.

**Rule: start the stack ONE way — either `robot-up.ps1` or a terminal, never
both.** `robot-up.ps1` now tears down and *verifies* zero remaining processes
before starting, so it is safe to run against a live stack.

### Tooling gotchas learned the hard way

- **`ros2 topic echo` gives false negatives.** It reported `/scan` SILENT while
  the bridge was demonstrably publishing at 7 Hz with 97% fill, and reported
  `/map` as not publishing for the same reason. `/map` is **latched**
  (`TRANSIENT_LOCAL`) so a default-QoS subscriber never receives it. Verify with
  a small rclpy subscriber with matching QoS, or from the bridges' own counters.
  This is the same class of unreliability already flagged for `ros2 topic hz`.
- **`pkill -f <pattern>` can kill your own shell** if the pattern appears in the
  command line you are running. `pkill -f imu_bridge.py` inside a `bash -lc`
  containing that string killed the shell before the replacement started.
- **`pgrep -cf <pattern>` self-matches** for the same reason, which produced a
  convincing but false "there are 2 of every node" report. Use
  `ps -eo pid,cmd | grep -E ...` and read the actual list.
- **Piping a here-string into `docker exec -i ... bash -s` fails silently**
  through PowerShell. The teardown never ran and left duplicate stacks. Write
  the script to a file, `docker cp` it, and run it by path.
- **`--output-dir` breaks `arduino-cli compile` for esp32 3.3.5** — a post-build
  hook copies `partitions.csv` from `<sketch>/build/<fqbn>/` and fails with exit
  1 even though the compile succeeded. Use `--export-binaries`.

## 2026-08-05 — Session 5: ESP32 TCP handshake (FIXED) + Docker port forwarding hit BOTH sensor ports (FIXED). There was no LiDAR fault.

**Symptom as reported:** `my-robot-server` never printed a successful ESP32
connection, video stream failed ("failed to grab frame" on the Pi), and SLAM
could not be tested at all.

**Three independent faults were stacked**, which is why this looked opaque.
Fixing one revealed the next.

---

### Fault 1 — ESP32 TCP handshake consumed by keepalive pings (FIXED)

**Root cause, proven:** The ESP32's `connectionMonitorTask` opens a
**payload-free** TCP connection to 5005 every second as a liveness check.
`RobotControlWorker::start()` accepted exactly **one** connection, required
`bytes_read > 0` to record the IP, then ran
`close(tcp_server_socket); // Stop listening on TCP completely`.

So on every server start a keepalive won the race within ≤1s, `read()`
returned 0 bytes, `--- Handshake Successful ---` never printed, `ARDUINO_IP`
stayed empty (`Invalid Arduino IP address format:`), and the listener closed
permanently. The ESP32 never recovered because its ping kept *succeeding*
(accepted by `com.docker.backend.exe` even with nothing listening in the
container), so `connected_to_server` never flipped false. It only ever
worked when the ESP32 booted fresh into an already-listening server — which
is why rebooting the ESP32 was the historical "fix."

**Fix applied (both halves are required):**

1. **Server** (`RobotControlWorker.cpp/.h`): loop `accept()` until a
   connection actually delivers a payload, hanging up on empty pings; stop
   closing the listener; set `O_NONBLOCK` and drain pending keepalives every
   main-loop iteration via a new `drainKeepalives()`; re-register
   `ARDUINO_IP` if a ping reports a different address; 1s `SO_RCVTIMEO` so
   `stop()` can interrupt; backlog 1 → 8.
2. **Firmware** (`sketch_dec30a.ino`): the keepalive now sends
   `WiFi.localIP().toString()` on **every** ping, not just the first
   handshake. The server cannot distinguish a ping from a handshake at
   `accept()`, so a payload-free ping would silently consume it.

**A bug introduced by the first version of my own fix, then fixed:** keeping
the listener open without continuing to accept caused keepalives to pile
into the backlog — measured ~10 sockets in `CLOSE_WAIT` and `rx_queue 9` on
the listener. With backlog 8 that would overflow and reproduce the original
symptom on a delay. Hence `drainKeepalives()`.

**Verification:**

| Test | Result |
|---|---|
| Fix #1 alone vs. old firmware | Listener stayed LISTEN through ~8 pings that would each have killed it |
| Both fixes, end-to-end | `--- Handshake Successful --- / Arduino IP address: 192.168.4.111` |
| 36s soak (30+ keepalives) | `LISTEN: 1, CLOSE_WAIT: 0` — no accumulation |

**Committed and pushed** as `fc15d06` on `master` of
`Personal-Tech-Projects/RobotServer`. Note `/root/code` inside the container
is **not** a git repo and **not** bind-mounted — the fixed files had to be
`docker cp`'d out into `C:\Users\jjlav\RobotServer` to be committed. That
local clone was also 4 commits behind and needed a `git pull --ff-only`
first. **The firmware change is still not committed anywhere.**

---

### Fault 2 — Docker UDP port forwarding never established (FIXED — 2nd occurrence)

**See `docker-port-forwarding-failure.md` for the full runbook.** Short
version: host UDP 5006 **and** 8888 had no listener at all while 5005
(`my-robot-server`) did, so every IMU and LiDAR packet was discarded by
Windows before reaching the container. `docker ps` claimed both were
published.

This is the **same failure as Session 4 (2026-08-01)**, which hit 5006 only
and where the restart fix was proposed but never actually applied. **This
time the restart was applied and confirmed to work.**

Key new evidence gathered this session:

- A throwaway container on alternate ports (15006/18888) proved the
  forwarding mechanism is completely healthy while Docker is settled —
  including 5/5 UDP packets delivered end-to-end to the container.
- Attempting to claim the *real* ports from that container was refused:
  `Bind for 0.0.0.0:5006 failed: port is already allocated` — direct proof
  of the daemon/host split-brain.
- Startup timeline shows `robot_brain` came up **11s into Docker Desktop's
  own boot** and is the container that failed, while `my-robot-server`,
  starting **one second later**, succeeded. The container that started
  *first* is the broken one, so any "first container claims the port"
  explanation is wrong — they use different ports and never compete.

**Correction to a claim made earlier in this session:** I asserted that
successfully binding 5006/8888 from PowerShell proved nothing was listening.
That reasoning is **invalid** — binding also succeeded on 15006/18888, which
were demonstrably in use by `com.docker.backend`. Windows permits sharing a
non-exclusively-bound UDP port. The conclusion happened to be right (netstat
independently showed the ports absent) but the proof was not. Use `netstat`.

---

### Fault 3 — NOT a LiDAR fault. Port 5006 mapping was missing (RESOLVED)

**CORRECTION: the "LiDAR sending nothing" diagnosis above was wrong about the
cause.** Critically, **the ESP32 was never plugged in by USB during that entire
phase**, so there was zero source-side evidence — everything was inferred from
receiver-side silence, the exact mistake the Session 4 lesson warns about.

Once the user connected the ESP32 by USB, a diagnostic firmware was flashed
(`[LIDAR-DIAG]`: counters for bytes read off `Serial2`, `0x54` headers matched,
and UDP packets sent, reported once per second) and the serial output read
directly:

```
[LIDAR-DIAG] avail=94 bytes=313747 headers=5092 sent=4920 first=54 54 54 F1 00 00 00 15 49 0B 68 E4
```

The ESP32 is reading ~16 KB/s off `Serial2` and transmitting **~250 LiDAR UDP
packets per second**. The LiDAR, its wiring, its ground, and the firmware are
all fine.

**The common-ground / TX-wire theory was WRONG — discard it entirely.** It was
built on an unverified inference, and there was never any evidence for it.

**The actual fault was another instance of Fault 2:** host UDP 5006 had no
mapping, so every LiDAR packet was discarded by Windows. Restarting
`robot_brain` *while LiDAR traffic was flowing* restored it, and a raw listener
inside the container immediately received real data:

```
LiDAR 5006 packets in 10s: 819   sample: 542c6f0819698900b48900b48900a488
IMU   8888 packets in 10s: 710   sample: b'ACC,-0.0039,0.07'
```

`542c` is the correct LD14P frame header (`0x54 0x2C`).

#### What is still genuinely unresolved

Why the earlier 12-second listener saw **0** LiDAR packets, when 5006 had been
confirmed bound moments before. Two candidates, not distinguishable after the
fact:

- **(a)** The LiDAR really was not transmitting then. The ESP32 was on non-USB
  power for that whole phase and its power situation changed when USB was
  connected. If so, 5006 was genuinely idle and the reaping theory holds.
- **(b)** 5006 was reaped in the ~1–2 minutes between the netstat check and the
  listener, and the LiDAR was transmitting all along. This would **contradict**
  the reaping theory, since traffic should have kept 5006 alive.

(a) is self-consistent with every other observation; (b) is not. But neither is
proven. **Open question for the user: was the USB cable connected before or
after you confirmed the LiDAR was spinning?** That single answer resolves it.

#### Operational note

If the LiDAR does depend on USB power, then unplugging the ESP32 may stop LiDAR
output while IMU keeps working — which is precisely the shape of the symptom
that started this hunt. Worth testing deliberately once the map is up.

#### Reminder

`sketch_dec30a.ino` still contains the `[LIDAR-DIAG]` instrumentation (build:
958003 bytes, vs. 957519 without it). Remove it and reflash once this is
settled. It is harmless but prints once per second forever.

### Other findings this session

- **NEW, observed but not yet diagnosed:** the ESP32's serial output shows
  `[Thread] Server ping failed. Connection lost!` repeating every ~5-10s,
  each time followed by a successful `TCP Connected! Sending IP...`. So the
  1s keepalive `connect()` intermittently fails while a fresh connect
  moments later succeeds. Note `com.docker.backend` accepts TCP 5005 even
  with nothing listening in the container, so a *failing* connect is not
  explained by the server being down (`ROBOTSERVER` was confirmed alive
  throughout). Possible WiFi contention — the ESP32 is transmitting ~250
  LiDAR packets/sec plus IMU at the same time.
- **Related, and more concerning:** every re-handshake runs `delay(100)`
  inside `loop()`, which stalls the `Serial2` drain. The diagnostic shows
  `avail` spiking from its normal ~100 bytes to **~2000** immediately after
  each handshake — against a 2048-byte RX buffer set by
  `Serial2.setRxBufferSize(2048)`. That is within ~50 bytes of overflowing
  and silently corrupting the LiDAR stream. If handshakes ever get more
  frequent, or the buffer smaller, this drops scan data. Worth making the
  handshake non-blocking.

- **ESP32 IP changed to `192.168.4.114`** (was `.111`, and `.78` back in
  Session 4). It is alive and pinging TCP 5005 once per second. The new
  `drainKeepalives()` re-registration handles this automatically, and the
  running binary was verified to contain the fix (`strings` finds
  `Arduino IP updated to`). **A DHCP reservation on the router would remove
  this variable entirely.**
- **Pi camera is physically absent** — no `/dev/video0` (only internal
  ISP/codec nodes video19–35), no webcam in `lsusb`, `rpicam-hello` reports
  none, zero camera lines in `dmesg`. Needs a physical replug. This is the
  cause of "failed to grab frame" in `webcam_streamer.py:36`
  (`cv2.VideoCapture(0)` → `/dev/video0`).
- **The visibility trap that hid all of this:** `ROBOTSERVER` and the ROS2
  stack run on `pts/2` under a VS Code integrated terminal (`STAT Sl+`, so
  they die on SIGHUP when the terminal closes). Their stdout never reaches
  `docker logs`, which only replays PID 1's (`/bin/bash`) stream. That is why
  the diagnostic `Invalid Arduino IP address format:` line was invisible the
  whole time. ROS2 node stdout is likewise absent from
  `/root/.ros/log/<run>/` — only `launch.log` is written there.
- **Known unfixed latent bug:** `WorkerManager.cpp:16-20` captures the
  range-for loop variable by reference in a lambda passed to `std::thread`
  (`for (auto& worker : asyncWorkers_) { std::thread t([&worker](){...}); }`)
  — dangling-reference UB. Investigated as a hypothesis this session and
  disproven as the cause of the handshake bug, but it is still real and
  still unfixed.

## 2026-08-01 — Session 4: RViz not mapping, traced to a DIFFERENT, directly-proven port-forwarding failure (not the TCP-gate bug, not ESP32/LiDAR hardware)

**Symptom:** user started both containers, launched the ROS2 stack, RViz
showed no map.

**Ruled out first:** confirmed `ros2 launch launch_all.py`'s full stack was
actually running (`ps aux` showed imu_bridge, main.py, rf2o, ekf_node,
slam_toolbox, rviz2 all up) — not simply "forgot to launch."

**Isolated to LiDAR side only:** `ros2 topic echo /imu/data --once` returned
a fresh, real sample immediately. `ros2 topic echo /scan --once` timed out
(5s, no message — should easily catch one at the LiDAR's ~6Hz rate if
healthy). Checked `/rosout` (via `stdbuf -oL timeout 8 ros2 topic echo
/rosout`, redirected to a file then grepped) for 8 seconds: zero log lines
from `lidar_bridge` at all (its "Received N packets" log only fires when
packets arrive), but `rf2o_laser_odometry` was continuously logging "Waiting
for laser_scans....". Conclusion at this point: zero raw UDP packets ever
reaching the container on port 5006.

**User pushed back, correctly, before accepting "ESP32 isn't sending":**
asked to check whether packets were even reaching the Windows PC first. Good
call — see `system-overview.md`'s new "A DIFFERENT, separately-confirmed
port-forwarding failure" section for the full technical writeup. Short
version:
- `Get-NetUDPEndpoint` (Windows host) showed a real listener for port 8888
  but **none for 5006**, despite `docker port robot_brain` claiming both
  were published.
- User then asked (fair, and I should have led with this instead of jumping
  straight to "restart the container"): *how do you know this is a port
  forwarding issue, explain in detail, and can you check if packets are even
  reaching the Windows side at all?* Required elevating to an Administrator
  terminal to use `pktmon` for a real answer instead of inference.
- `pktmon` packet capture (filtered to port 5006, ~15s window) showed
  **continuous real packets arriving at the Windows NIC** throughout:
  `192.168.4.78.4210 > 192.168.4.81.5006: UDP, length 47` — confirmed the
  ESP32's real IP, the right port, and the right byte count
  (`PACKET_SIZE = 47` in the firmware).

**Conclusion, now directly proven rather than inferred:** ESP32 sends
correctly, packets physically reach the Windows PC, and then get silently
dropped because nothing on Windows is listening on 5006 to hand them into
WSL2/Docker/the container. This is the same *class* of failure the
WSL2-NAT-forwarding theory predicted back in Session 2 (and which turned out
to be a red herring for THAT symptom, which was actually the TCP-gate bug)
— but this time it's a real, separately-occurring instance, isolated to one
port, with direct proof instead of guesswork.

**Fix not yet applied as of this entry** — proposed restarting `robot_brain`
to force Docker to recreate port publishing (established fix from the
earlier resilience stress test), pending user go-ahead. Update this entry or
add a new one once that's tried and confirmed/denied.

**Lesson reinforced (same shape as the `rx_queue` mistake earlier in this
investigation):** absence of evidence (no listening socket) is not proof of
absence of an event (packets arriving) — always check with a lower-level,
direct tool (`pktmon` here, `nc -ul` there) before concluding a sender-side
problem from receiver-side silence.

## 2026-08-01 — Session 2 continued further: CONFIRMED WORKING END-TO-END

**TL;DR: the original goal of this whole investigation (does IMU data reach
the EKF and contribute to the SLAM map?) is now answered YES, confirmed live.**
Also: the "port-mapping/Docker/WSL2" theory from a few entries ago was WRONG
— see the correction below. Read this entry fully before assuming anything
about future connection issues from this point forward.

**User pushback that led to catching my error (important reasoning lesson):**
After the firmware fix, `ros2 topic hz /imu/data` and `/scan` both still
showed zero messages, even after a container restart. I concluded this meant
Docker/WSL2 was still dropping packets between Windows and the container,
based on `/proc/net/udp` showing `rx_queue=0, drops=0` on both sockets. The
user pointed out that raw `nc -ul <port>` used to work fine *inside this same
container* before, and asked why it would suddenly be a port-mapping issue.
That was the right instinct — I was wrong. **`rx_queue=0` on a UDP socket
does NOT mean "nothing has arrived" — it only means "nothing is currently
buffered and unread at this instant."** A healthy socket that's being read
immediately by its owning process will *always* show `rx_queue=0`, indistin-
guishable from one receiving nothing at all. I conflated "empty queue right
now" with "never received anything," which is simply incorrect. Lesson:
don't trust an instantaneous queue-depth snapshot as evidence of an empty
stream — check with something that actually confirms receipt (`nc`, or
`topic echo`), not a point-in-time counter with no baseline.

**What actually happened, once tested correctly:**
1. Live `nc -ul 8888` (after killing `imu_bridge.py` to free the port)
   immediately received a real packet: `ACC,-0.0078,-0.0117,0.0117`.
2. Live `nc -ul 5006` immediately received real (binary) LiDAR frames.
   **Both confirm the network path — ESP32 → Windows → WSL2 → container —
   is completely healthy.** The earlier WSL2-NAT/Docker-forwarding theory
   from the previous session entry was a red herring for *this* symptom
   (it may still be real for some *other* failure mode, but wasn't what was
   happening here — don't rule it out entirely, just don't assume it's the
   default explanation going forward without fresh evidence).
3. Restarted `imu_bridge.py` and `main.py` directly (not via full
   `ros2 launch`) with `python3 -u` for unbuffered output, redirected to
   `imu_bridge_debug.log` / `lidar_debug.log` in this directory. Watched them
   correctly parse real data: `ACC -> x: -0.0078, y: 0.0547, z: -0.1055`,
   `ROT -> w: 0.3446, x: 0.0625, ...`, and `Decoder Success! Current start
   angle: ...` for LiDAR. **The bridge code itself has no bug.**
4. Still, `ros2 topic hz /imu/data` and `/scan` showed nothing. Checked
   `ros2 topic info /imu/data --verbose`: publisher (`imu_bridge`,
   RELIABLE) and subscriber (`ekf_filter_node`, BEST_EFFORT) both exist and
   are discovered — that QoS combination IS compatible per DDS rules
   (BEST_EFFORT-requesting subscribers can receive from RELIABLE
   publishers; only the reverse is incompatible). So discovery and QoS were
   both fine — `ros2 topic hz` itself was the unreliable signal.
5. Switched to `ros2 topic echo /imu/data --once` (and same for `/scan`) —
   **both immediately returned genuine, well-formed messages** with real
   sensor values matching what the bridge scripts had just parsed.
6. Checked `ros2 topic echo /odometry/filtered --once` — EKF is genuinely
   publishing a fused state estimate, and its orientation quaternion
   (`z: 0.9378, w: 0.3473`) closely tracks the raw IMU orientation
   (`z: 0.9308, w: 0.3448`) moments earlier — **direct evidence the EKF is
   actually fusing the IMU orientation into its output**, not just
   publishing something stale/unrelated.

**Why `ros2 topic hz` was lying to us (best current explanation, not 100%
proven but consistent with everything observed):** `ros2 topic hz` is
itself an rclpy/Python CLI tool. Every check I ran wrapped it in
`timeout N ros2 topic hz ...` via `docker exec ... bash -c "..."` — a
non-TTY context. If `timeout` kills it with SIGTERM before it flushes
whatever it was about to print, and Python fully-buffers stdout when not
attached to a TTY (the exact same issue we already diagnosed for
`imu_bridge.py`'s own `print()` calls, much earlier in this investigation —
see the "Gotcha" section in `commands.md`), the tool could have been
computing/updating a real rate internally the whole time while never
successfully printing it before being killed. **Prefer `ros2 topic echo
--once` for verification in a non-interactive/piped context** — it exits
normally after one message rather than being killed, so it doesn't hit this
buffering trap.

**Current overall status: everything is confirmed working END TO END right
now**, with the ESP32 running the clean (marker-removed) firmware fix, the
robot_brain container in its current (restarted) state, and `my-robot-server`
still down (proving the fix's whole point — IMU/LiDAR streaming no longer
needs it). Two loose ends before calling this fully closed:
1. The currently-running `imu_bridge.py`/`main.py` were started manually
   (`python3 -u ... > .../brain/*_debug.log`), NOT via the normal
   `ros2 launch launch_all.py` path — functionally equivalent, but if you
   want the "normal" launch running instead, stop these two and re-run
   `ros2 launch launch_all.py` from `/root/code`, then re-verify with
   `topic echo --once` (not `hz`).
2. `my-robot-server`'s reliability (interactive-shell container, no restart
   policy, dies on terminal disconnect) is still an open, unresolved issue
   for motor control / manual override — see the earlier entry in this log
   and `windows-docker-networking.md`. Not addressed this session; the
   firmware fix means it no longer blocks sensor streaming, but it still
   needs fixing for motor control to work reliably.

## 2026-08-01 — Session 2 continued: real root cause found + firmware fix deployed

**Correcting my earlier claim.** I initially said "the ESP32 isn't sending
anything" based only on `ros2 topic hz` showing zero messages — user rightly
pushed back that I hadn't actually verified that, only that nothing arrived
on the ROS2 side. Lesson: don't conflate "no evidence of X" with "confirmed
not-X" — say what was actually tested.

**Packet capture (Windows host, `pktmon`, admin required):**
- Set up filters for UDP 8888/5006, ran captures before and after having the
  user power-cycle the ESP32. Both captures came back with **zero real
  packets** — only pktmon's own startup metadata dump (1279 lines every
  time, byte-identical structure). Confirmed this is genuinely "nothing
  arrived," not a tooling artifact, by checking for `Frame:`/`Ethernet`
  markers and finding none beyond component-topology boilerplate.
- `pktmon` requires an elevated (Administrator) terminal — the user actually
  restarted this whole Claude Code session via `claude --continue` in a new
  elevated window to get this (note: `--continue` resumes by *working
  directory*, so if it ever says "no conversation found," `cd` to the
  original directory first, or use `claude --resume` for a picker).

**Serial monitor (the actual smoking gun):** ESP32 plugged into this Windows
host via USB, read directly over its COM port using .NET's
`System.IO.Ports.SerialPort` from PowerShell (no separate serial-monitor app
needed — see `esp32-deployment.md` step 7 for the exact snippet). Output,
repeating forever:
```
Attempting TCP handshake with server: 192.168.4.81
TCP Handshake failed. Is the C++ server running?
```
WiFi connects fine (it gets past `setup()`'s WiFi-connect blocking loop) —
the ESP32 is stuck permanently failing the TCP:5005 handshake. Per the
firmware's `loop()` structure (see `system-overview.md` "TCP gate"), a failed
handshake hits an early `return` — **IMU and LiDAR sending code never runs
at all** while this is happening. This fully explains the zero-packet
capture above; it has nothing to do with Docker/WSL2/robot_brain.

**Found the actual missing piece:** `docker ps -a` (not just `docker ps`)
revealed a **third container, `my-robot-server`** (image
`blavigne0/robot-dev:v1`), publishing **TCP+UDP 5005** — this is what's
supposed to answer the ESP32's handshake. Status: `Exited (255) 4 hours ago`.
`docker logs my-robot-server` showed only an interactive bash prompt (someone
typed `ipconfig`, which doesn't exist on Linux, then nothing) — **this
container's command is literally `/bin/bash`, not a persistent service.**
Exit code 255 is the classic signature of a terminal/TTY disconnecting. No
restart policy, no supervisor — whenever whatever terminal was attached to it
disconnects (window closed, sleep, etc.), the container and whatever server
process was manually started inside it both die together.

**This is very likely the real "sometimes I have to restart the docker
container" mechanism** — not the WSL2-NAT theory from earlier in this
session (that may still be a real secondary issue for `robot_brain`'s ports,
but it isn't what was happening this time). Whoever normally "fixes" this
probably restarts/re-enters `my-robot-server` (or all containers broadly),
not `robot_brain`.

**Decision made with user:** rather than chase `my-robot-server`'s
reliability further right now, fix the ESP32 firmware so it doesn't need
that server up at all for IMU/LiDAR streaming (motor control can stay
dependent on it, that's a separate, lower-stakes concern).

**Firmware fix (deployed and verified working):**
- Found the real compilable project on the Windows host (the ROS2 repository
  intentionally does not contain a duplicate firmware snapshot):
  `C:\Users\jjlav\Documents\Arduino\RobotController\sketch_dec30a\sketch_dec30a.ino`
- Restructured `loop()`: the TCP:5005 handshake retry is now rate-limited
  (`millis()`-gated, once per 2s) and **no longer `return`s** — IMU read/send
  and LiDAR read/send now run every loop iteration unconditionally. The
  manual-override UDP check is now guarded behind `connected_to_server` (it
  wasn't safe to call `Udp.parsePacket()` before `Udp.begin()` had run).
  Motor control / manual override still correctly depends on the handshake
  succeeding — only sensor streaming was decoupled.
- Compiled + flashed via `arduino-cli` (bundled with Arduino IDE — see
  `esp32-deployment.md` for exact paths/commands). Board: DOIT ESP32 DEVKIT
  V1, FQBN `esp32:esp32:esp32doit-devkit-v1`, port `COM3`.
- **Verified the deploy pipeline itself actually works** (user's request,
  good call): added a temporary unique marker (`CLAUDE-TEST-8842`) as both a
  one-time boot print and a repeating 3s heartbeat, reflashed, confirmed via
  serial that the heartbeat was genuinely repeating (stronger evidence than
  the one-time boot line, which can be missed due to `Open()`'s reset
  timing). Then reverted the markers and recompiled — byte count
  (957399 bytes / 50456 bytes globals) matched the pre-marker build exactly,
  confirming a clean revert. Full repeatable procedure now documented in
  `esp32-deployment.md`.

**STATUS WHEN INTERRUPTED — important, check this before assuming anything:**
The clean (marker-removed) build was recompiled successfully, but the
`arduino-cli upload` command to reflash it was **interrupted by the user
before running** (they wanted documentation written first — this entry is
that documentation). **This means the ESP32 may currently still be running
the TEST-MARKER build** (with the temporary heartbeat prints), not the clean
final version. **Pick up here:**
1. Re-run the upload command from `esp32-deployment.md` step 6 to flash the
   clean, reverted `sketch_dec30a.ino` (verify no markers are present in the
   file first — they were removed via Edit but never reflashed).
2. Read serial for ~15s and confirm NO `CLAUDE-TEST-8842` lines appear
   anymore, and handshake-retry messages still look normal.
3. THEN re-do the packet capture (pktmon, ports 8888/5006) to confirm real
   `ROT,`/`ACC,` UDP packets now reach Windows even with `my-robot-server`
   still down — this was in progress (capture `esp32_capture3.etl` was
   started and stopped, but the format+analysis step was interrupted before
   completing).
4. If packets ARE now arriving at Windows: relaunch the ros2 stack (see
   `commands.md`) and check `/imu/data` and `/scan` rates — this finally
   gets us back to the ORIGINAL Session 1 question (does IMU data reach the
   EKF/SLAM map).
5. Talk to the user about `my-robot-server`'s reliability (interactive-shell
   container, no restart policy) as a separate follow-up — motor
   control/manual override still depends on it.

## 2026-08-01 — Session 2: Windows-host Docker networking investigation

**Context:** moved to a Claude Code session running directly on the Windows
host machine (`C:\Users\jjlav`) that runs Docker Desktop, specifically to get
`docker start/stop/restart` and config access that wasn't available from
inside the container. Priority for this session: root-cause **why restarting
the docker container sometimes fixes the ESP32 connection** (separate from
the IMU-vs-EKF question, which is still open — see Session 1 above).

**Container state:** `robot_brain` (image `ros2-slam-bot:latest`, id
`32675c04d75e`) was still running, "Up 2 hours" — it was never restarted
across the session handoff. However, the actual `ros2 launch launch_all.py`
process from Session 1 had died (only VS Code server processes + a leftover
`ros2-daemon` were alive in `docker exec robot_brain ps aux`) — this was
almost certainly killed when the shell session inside the container was torn
down for the handoff, not a real finding. Needs a fresh `ros2 launch` to test
live again.

**Docker networking architecture (this is the important part):**
- `docker info` -> `Kernel Version: 6.6.87.2-microsoft-standard-WSL2`. Docker
  Desktop is running on the **WSL2 backend**. Traffic path for the published
  UDP ports is: ESP32 -> Windows Wi-Fi NIC -> Windows host network stack ->
  Docker Desktop's port-forwarding process -> WSL2 Linux VM -> Docker bridge
  network -> container's veth -> container socket. Every extra hop here is a
  place UDP forwarding state can go stale (UDP has no keepalive/handshake to
  detect and recover a broken mapping, unlike TCP).
- Host's Wi-Fi adapter (`Wi-Fi`, not the other 2 disconnected Wi-Fi adapters)
  has IPv4 `192.168.4.81` — confirmed this **is** the IP the ESP32 firmware
  targets (`server_ip` in the .ino). Correct interface.
- Windows is currently listening on UDP `0.0.0.0:8888` and `:5006` (and `[::]`
  equivalents) under PID `32916` = `com.docker.backend.exe` (Docker Desktop's
  own forwarding process, confirmed via `Get-Process`). This is healthy right
  now — not proof it stays healthy indefinitely.
- **No `%USERPROFILE%\.wslconfig` file exists** -> WSL2 is running in its
  **default NAT networking mode**, not the newer opt-in "mirrored" mode.
  Default NAT mode is the specific configuration with a long, well-documented
  history of exactly this symptom (Docker Desktop + WSL2 + UDP port forwarding
  silently stops working after network changes/sleep/idle, restart fixes it).
  **Mirrored networking mode is Microsoft's own documented fix for this class
  of bug** — the WSL2 VM shares the host's network interfaces directly instead
  of going through NAT, removing most of the failure-prone hops above.
- Wi-Fi's Windows network profile is currently **"Public"**, not
  Private/Domain. Public profile gets much stricter default Windows Firewall
  inbound rules. Checked `Get-NetFirewallRule -DisplayName "*Docker*"` — there
  ARE inbound Allow rules named "Docker Desktop Backend" scoped to Profile
  `Public`, Enabled `True` — so the firewall is *currently* configured
  correctly and is probably not the active blocker right now. Still flagged as
  a plausible trigger: if Windows ever re-evaluates/reclassifies the network
  (sleep/wake, router reboot, reconnect), a stricter profile could transiently
  block inbound UDP until something (e.g. a container restart, which re-does
  the port publish) shakes it loose. Lower-confidence than the NAT-mode
  finding, but worth keeping in mind.

**Leading hypothesis (highest confidence): WSL2 default NAT networking mode's
known UDP-port-forwarding instability.** Not yet proven with a live
before/after test — see next steps.

**Ruled out / de-prioritized:**
- Orphaned-process-holding-the-UDP-port theory (from Session 1) — checked
  `/proc/net/udp` inside the container earlier and both sockets were cleanly
  bound with no conflicts. Possible but not the primary suspect anymore.

**Next steps (in order):**
1. Relaunch `ros2 launch launch_all.py` inside the container (via
   `docker exec robot_brain ...` or re-entering the container shell) to get a
   live stack running again.
2. Get a genuine live baseline: user powers on the ESP32, confirm via its
   Serial monitor that the TCP:5005 handshake succeeds, then check
   `ros2 topic hz /scan` and `/imu/data` from a state where the container has
   NOT been recently restarted. This tells us whether the current NAT/firewall
   state is actually working right now.
3. **The real test**: next time the connection issue reproduces naturally (or
   if the user wants to force it, e.g. by putting the Windows machine to
   sleep/wake or toggling Wi-Fi), capture: `netstat -ano | findstr ":8888 :5006"`
   on Windows (does `com.docker.backend.exe` still hold the ports?), and
   `docker exec robot_brain` check of `/proc/net/udp` (does the container-side
   socket still look fine?) — BEFORE restarting anything. Then restart and
   recapture. This tells us definitively which layer actually breaks.
4. **Proposed fix to discuss with user**: create `%USERPROFILE%\.wslconfig`
   with `[wsl2]\nnetworkingMode=mirrored`, then `wsl --shutdown` and restart
   Docker Desktop, and see if that eliminates the need for periodic container
   restarts going forward. This is a config change outside the container/repo
   — get explicit user sign-off before making it, and note it changes WSL2
   networking for ALL WSL2 usage on this machine, not just this container.
5. Once the restart-cause is understood, return to the original IMU-vs-EKF
   question (Session 1's unfinished work).

## 2026-08-01 — Session 1: initial code review + first live run

**Goal:** figure out whether IMU data from the ESP32 actually reaches the EKF
and contributes to the SLAM map. User confirms LiDAR points show up in RViz;
no confirmed evidence yet for IMU.

**Static code review (before running anything):**
- Read `imu_bridge.py`, `ekf.yaml`, `launch_all.py`, `launch_odometry.py` — topic
  names match (`imu/data` publisher == `/imu/data` in ekf.yaml `imu0`), static
  transform for `base_link->imu_link` exists, covariances are set correctly
  (non-zero diagonal, not `-1`), so nothing obviously wrong on paper.
- `git log` shows 3 relevant commits: `9ff760a` (added ekf.yaml + odometry
  changes), `125abfd` (added imu_bridge.py, **removed microros agent**),
  `aea3296` (fixed base_link->imu_link transform duplication).
- Got the ESP32 Arduino source from the user. Found the TCP-5005 "gate" (see
  `system-overview.md`) — initially thought this fully blocked IMU (and LiDAR)
  from ever sending. **User corrected this**: there are two servers (a
  non-SLAM "robot server" that answers the TCP handshake + motor commands, and
  this "ROS2 server" container). Since LiDAR is confirmed working, the gate is
  presumably satisfied by the robot server and is likely not the IMU-specific
  bug. Do not re-litigate this without new evidence.
- Checked `/root/.ros/log/` — old sessions (last run ~2026-07-30, nothing from
  today yet), only capture launch-level INFO lines, not node stdout. Not useful
  for today's live debugging.

**Live run:**
- Confirmed `ros2` CLI present (`/opt/ros/humble/bin/ros2`, ROS2 Humble).
- Confirmed nothing was already running (`ps aux` clean) before launch.
- Ran `ros2 launch launch_all.py` from `/root/code` in background, log ->
  `/tmp/.../scratchpad/ros2_launch.log`.
- All 7 expected processes came up: `imu_bridge.py`, `main.py`, 2x
  `static_transform_publisher`, `rf2o_laser_odometry_node`, `ekf_node`,
  `async_slam_toolbox_node`, `rviz2`.
- Log file came back empty — **not a bug**, just Python fully-buffers stdout
  when it's not a TTY, so `imu_bridge.py`'s `print("ROT -> ...")` debug lines
  won't show up in the redirected log file until the buffer flushes. Need to
  use `ros2 topic echo`/`hz` directly instead of relying on that log, or
  restart the node with `python3 -u` for unbuffered output if we need the logs.

**Topic-level check (with stack up, before ESP32 was confirmed connected):**
- `ros2 topic list` — `/imu/data` IS present in the graph (publisher exists).
- `timeout 8 ros2 topic hz /imu/data` -> zero messages in 8s window.
- `timeout 8 ros2 topic hz /scan` -> also zero messages in 8s window.
- **Conclusion: this is NOT evidence of an IMU-specific bug.** Both LiDAR and
  IMU are silent, which just means the ESP32 isn't currently sending anything
  to this container at all (consistent with "haven't run lidar project yet
  today"). Need the robot actually powered on/connected before topic checks
  mean anything. Asked user to power it on; paused here.

**Status when session paused:** ros2 stack still up (background task
`baqoleorm`, log at `/tmp/.../scratchpad/ros2_launch.log`). Waiting on user to
power on/connect the ESP32 so `/scan` and `/imu/data` actually have live data
to compare. **Pick up here** — re-run the hz checks below once robot is live.

**Next steps (in order):**
1. Once ESP32 is confirmed connected: `ros2 topic hz /scan` and
   `ros2 topic hz /imu/data` again. If `/scan` flows and `/imu/data` doesn't,
   that's the real signal — proceed to step 2. If both are still silent,
   the robot isn't actually connected yet — troubleshoot that first (may be
   the docker-restart issue, see below).
2. If IMU specifically silent while LiDAR flows: check whether UDP 8888
   packets are even arriving at this container (tcpdump/socket stats) vs. a
   code-level bug in `imu_bridge.py`'s parsing.
3. Check EKF is actually fusing it: `/diagnostics`, `ros2 topic echo
   /odometry/filtered`, `ros2 run tf2_ros tf2_echo base_link imu_link`.
4. Try to reproduce the "restart docker to fix connection" bug on purpose
   (kill imu_bridge.py uncleanly, see if a relaunch fails to bind port 8888) —
   **ask the user before doing this**, it's disruptive.

---

## Session 6 — 2026-08-06 — ESP32 reboot loop (power)

**Symptom:** after a clean stack bringup, `lidar_bridge` briefly showed healthy
counters (32 dgram/s, 5 scans/s, fill 343/360, rejected=0) then both bridges
went to `NO UDP packets arriving`. `imu_bridge` never received anything at all.

**What it was NOT:** not the Docker port mappings (all four of tcp5005 /
udp5005 / udp5006 / udp8888 were MAPPED throughout, verified with netstat before
and after), not a duplicate stack (single instance of every node, PIDs 125-138),
not the bridges (single instances, both logging their own counters).

**Root cause: the ESP32 was rebooting about every 7 seconds, then died outright.**
The board was running from the power bank. Confirmed by two independent signals:

1. **Its own UDP log (port 8890).** The `[DIAG]` counters climb for ~2.7 s then
   restart from zero, with ~4 s of total silence in between (boot + WiFi
   reconnect, during which UDP logging is not yet available):

       14:11:55  [IMU] sensor reset itself - re-enabling reports
       14:11:55  [DIAG] lsent=5  ... isent=0
       14:11:56  [DIAG] lsent=39 ... isent=0
       14:11:57  [DIAG] lsent=72 ... isent=0
       (4 s gap)
       14:12:01  [IMU] sensor reset itself - re-enabling reports
       14:12:01  [DIAG] lsent=6  ... isent=0

   `isent=0` every cycle: the board never stayed up long enough for the BNO085
   to produce a single event, which is also why `[IMU] sensor reset itself`
   appears every cycle - that is just the sensor's power-on reset flag being
   read after each fresh boot, NOT a sensor fault. Do not chase the IMU here.

2. **The Windows TCP table.** New technique, worth reusing. The ESP32 opens
   connections to port 5005; `netstat -ano -p TCP` showed ~15 clusters of
   exactly 4 TIME_WAIT entries with contiguous local ports, and the ephemeral
   port range reseeded between clusters (50966 -> 53200 -> 55810 -> 57276 ->
   57586 -> 58703 -> 59064 -> 61268 -> 62094 -> 64484). A board that stays up
   allocates ports sequentially; **a reseeded ephemeral range means the IP stack
   restarted, i.e. the board rebooted.** ~15 clusters inside the ~2 min TIME_WAIT
   window agrees with the ~7 s period seen in the DIAG log.

The board then stopped transmitting entirely: no new TCP connections, and a 15 s
listen on UDP 8890 received 0 lines, while the discovered IP stayed 192.168.4.25
(that address is inferred from aging TIME_WAIT rows, so **`Get-Esp32Ip` returning
an address is not proof the board is alive** - cross-check with a log listen).

**Fix:** power the ESP32 from USB rather than the power bank. The same firmware
ran 7+ minutes uninterrupted earlier the same day, and nothing was reflashed in
between, so the change is in the supply, not the code.

**Still not implemented:** `esp_reset_reason()` logging in `setup()`. It would
have distinguished brownout from panic/watchdog in one line instead of requiring
the inference above. Note it cannot be added over OTA while the board is in a
reboot loop - OTA needs more uptime than the loop allows, so this must be
flashed over USB.

**Also note:** the ESP32 IP had drifted again, .114 -> .25. Runtime discovery
(`Get-Esp32Ip`) handled it; no hardcoded address needed updating.

### X11 after a container restart

Restarting `robot_brain` clears the live X sockets in `/tmp/.X11-unix`, so
`robot-up.ps1` finds no DISPLAY and rviz2 exits immediately. The sockets are
created by the VS Code remote session's X forwarding, so they can only be
restored by attaching VS Code to the container again - there is no way to
recreate them from inside. Everything headless (bridges, EKF, rf2o,
slam_toolbox, ROBOTSERVER) comes up fine without it; only rviz2 is affected.

### Session 6 addendum — two follow-on faults after the ESP32 was fixed

**1. The SLAM map must be discarded after any upstream sensor fault.**
slam_toolbox had been running throughout the ~35 min the ESP32 was reboot
looping, so it spent that whole time stamping scans against garbage poses. The
result was an `/map` of **22059 x 29312 cells = 1103 x 1465 m, 646 million cells,
0.0% known** - the same scattered-debris pathology seen with the duplicate-stack
and teleport bugs. Restarting the stack cleared it; the fresh map came up at
137 x 102 cells = 6.9 x 5.1 m, 7.5% known, which is a sane room.
**Rule: fixing the sensor is not enough - the map built during the fault is
unrecoverable and the stack must be restarted afterwards.**

**2. `robot-up.ps1` could leave two ROBOTSERVERs running.** Step 5 used
`pkill -f ROBOTSERVER`. `-f` matches the full command line, which includes the
command line of the `bash -lc` shell running the pkill itself, so the shell
SIGTERMed itself (docker exec returned 143) before `sleep 1` and the real
ROBOTSERVER survived - then the next line started a second one. Both would bind
port 5005 and the ESP32 handshake lands on whichever wins the race. This is the
third time this exact self-match bug has appeared (previously with
`pkill -f imu_bridge.py` and `pgrep -cf`).
Fixed to `pkill -x ROBOTSERVER` (exact process NAME, so the shell cannot match),
plus a verify-and-`SIGKILL` fallback. **The fallback fired on the very first
run**, so `-x` alone was not sufficient - keep the escalation.

**Rate measurement caveat.** A first health check reported /scan 2.4 Hz and
/imu/data 47 Hz and looked like heavy loss. It was the measuring script:
a `spin_once(timeout_sec=0.2)` loop cannot drain a 150 Hz topic. Re-measured
with a `MultiThreadedExecutor` spinning on a background thread:

    /scan               6.1 Hz    (bridge reports 6-7 scans/s - agrees)
    /imu/data         151.1 Hz    (ESP32 isent ~150/s - agrees, no loss)
    /odom_rf2o          6.0 Hz
    /odometry/filtered 28.4 Hz
    scan fill         345/360 valid returns
    IMU yaw           range 0.01 deg, max step 0.01 deg

**Always spin on a background thread when measuring rates**, or the sampler
becomes the bottleneck and manufactures a loss report.

### Session 6 — power split resolved; residual loss is WiFi, not corruption

**Fix confirmed: ESP32 on its own fully-charged power bank, motors/LiDAR on the
second bank.** Two stationary tests, 75 s and 45 s, with zero resets - counters
climb continuously (lsent 17788 -> 19256 at a steady 33.4/s, isent at 152/s).
Compare the failing case, which never got past lsent=73 before rebooting.
Grounding is fine: a floating ground between two banks would inject noise into
the I2C-fed IMU, and yaw held to 0.02 deg range / 0.01 deg max step.

**No corruption anywhere.** Three independent checks:
- `lidar_bridge` reports `rejected=0` on every line since startup (per-frame
  checksum validation).
- `/proc/net/snmp` `InCsumErrors = 0`, no change over 30 s.
- IMU yaw steady to 0.02 deg; a corrupted quaternion would be visibly wild.
UDP checksums mean a corrupted packet is discarded by the kernel before the
application sees it, so **corruption always presents as loss, never as bad
data.** Stop looking for corrupted values; look for missing ones.

**Residual loss is real but modest, and it is NOT on the robot.** The bridge
saw dips to 2 and 7 dgram/s while the ESP32's own counters showed an unbroken
33-34/s across the same window - the transmitter never faltered, so the packets
died after leaving it. Ruled out on the receive side:
- **Not socket buffer overflow:** `RcvbufErrors` flat at 552 over 30 s (that 552
  is cumulative since container boot, none of it recent).
- **Not CPU starvation:** robot_brain at 123% of 3200% (32 cores), load avg 5.5.
That leaves the air. RSSI fell from -62/-64 on USB at the desk to -71/-79 on
battery across the room, and ESP32-side TX failures resumed at ~0.6% (lfail
652 -> 661 in 45 s), clustering on the RSSI dips.

**Signal margin is now the limiting factor, replacing power.** Expect it to
worsen with distance from the AP; watch for RSSI approaching -80. Symptom to
recognise: brief 1-3 s collapses in `dgram/s` with full recovery afterwards,
and scan `fill` dropping from ~345/360 to ~220-290.

**Diagnostic worth reusing:** to decide whether loss is the robot's fault,
compare the ESP32's own `lsent` delta against the bridge's `dgram/s` over the
same seconds. Steady `lsent` + dipping `dgram/s` = the link, not the board.

## Session 6 — the twist rejection threshold caused a far worse failure than it fixed

**This entry corrects the Session 5 conclusion. `odom0_twist_rejection_threshold:
1.0` was a bad value and the stationary test that validated it was incapable of
detecting the problem.**

**Symptom while driving:** 3164 pose-jump events in 127 s (vs 1 event in the
stationary test that "validated" the fix), and a map blown up to 198.6 x 260.8 m.
Every jump had the same signature: `dpos = 0.15-0.31 m` in `dt = 0.033 s`,
`dyaw = 0.0`, tagged "IMU steady".

**What it was NOT:** not rf2o teleports - the spike detector counted **0**
instances of rf2o linear velocity exceeding 2 m/s across the whole run, so the
odometry input was sane. Not corrupted data, not packet loss (scan 5.9 Hz,
fill 345/360, imu 148 Hz throughout). Not gravity leaking into the accelerometer:
measured |a| over 1900 samples was **0.051 m/s^2**, i.e. the firmware publishes
essentially zero linear acceleration.

**Root cause - a rejection-threshold lockout:**
1. The EKF velocity state diverged to ~5.9 m/s during driving.
2. rf2o then reported the truth (<2 m/s). That measurement is an enormous
   Mahalanobis distance from a 5.9 m/s prediction.
3. `odom0_twist_rejection_threshold: 1.0` discarded it - **and every subsequent
   correction**, because the state never moves back toward the measurements.
4. `imu0_config` had `ax, ay` enabled while the firmware sends ~0 acceleration,
   so the filter was simultaneously told "you never accelerate", pinning the
   velocity state in place.

Measured proof: EKF speed **mean 5.89 m/s, max 5.91 m/s - constant, not
growing**. A constant wrong velocity is the signature of *no correction being
applied at all*; an integration runaway would grow instead. Position then runs
away linearly forever, and slam_toolbox stamps scans metres from reality.

**The threshold is a Mahalanobis distance in standard deviations.** At 1.0 it
rejects roughly a third of perfectly good measurements even in normal operation.
Sane values are 5-10. It is also self-reinforcing: the further the filter
diverges, the more certainly it rejects the corrections that would fix it.

**Fix applied** (backup: `ekf.yaml.bak-2026-08-06-driving`):
- `odom0_twist_rejection_threshold: 1.0` -> `5.0`
- `imu0_config` ax, ay -> `false` (IMU now supplies **yaw only**, rf2o supplies
  velocity - the standard configuration)

**Result, same 127 s test, same route:** 3164 events -> **7**, of which **0 are
pose jumps** (all 7 are lidar scan gaps). Map 198.6 x 260.8 m -> **6.0 x 8.9 m**.

### Methodological lesson - do not repeat this

**A stationary test cannot validate a filter change.** When nothing moves,
predicted state and measured state agree almost exactly, so a rejection
threshold never fires and looks harmless. The Session 5 claim of a "30x
improvement, 1 jump in 8381 messages" compared a *stationary* run against a
*driving* baseline of 55 jumps - not a like-for-like comparison, and it hid a
regression that was far worse than the original bug. **Any change to ekf.yaml
must be validated while driving, over the same kind of route as the baseline.**

Also: `spikes 0` is the fastest way to tell these two failure modes apart.
rf2o teleports show large rf2o velocities; a filter lockout shows sane rf2o
velocities with a diverged filter output. Always measure the *input* separately
from the *output*.

### Session 6 — EKF fix CONFIRMED under driving (valid test)

Re-tested after the ekf.yaml corrections, this time verifying the robot actually
moved (`driven` metric added to the watcher precisely because the two previous
"verifications" were stationary and therefore worthless).

    === DONE: driven 25.4m, jumps 1, diverge 2, gaps 24, maxdiv 4.62 m/s ===

    broken run:  3164 jumps / 127 s, EKF locked 5.89 m/s, map 198.6 x 260.8 m
    fixed  run:     1 jump  / 423 s, EKF 0.123 m/s,       map  17.5 x  18.5 m

**The decisive measurement is the paired speeds, not the jump count:**

    EKF  speed mean 0.123  max 0.275 m/s
    rf2o speed mean 0.124  max 0.264 m/s   <- agree to 0.001 m/s

Always measure filter output against filter input as a pair. Watching only the
output is what let the lockout run undetected for a whole session.

**Both DIVERGE events were the benign direction** (rf2o spiking, EKF correctly
rejecting). Direction is what matters:
- EKF **above** rf2o = filter locked out, ignoring reality = THE BUG
- EKF **below** rf2o = filter rejecting a bad scan match = WORKING AS INTENDED

Cheap way to tell them apart without direction logging: a filter locked at
V m/s advances V*0.033 m every message, so any lockout above ~4.5 m/s trips a
0.15 m jump detector on *every message*. If the jump count stays flat, the
filter is not locked, whatever the divergence magnitude.

Map behaviour also correct: dimensions plateaued at 17.5 x 16.9 m while known
cells kept rising (11847 -> 14884), i.e. the second pass landed on top of the
first instead of ghosting beside it.

### Open issue: LiDAR dropouts are PERIODIC, not random

24 gaps in 423 s, and the timestamps are strikingly regular - **pairs of gaps
(~0.86 s then ~1.11 s) every ~32 seconds**:

    23:14:19 + 23:14:21      23:15:56 (single)
    23:14:51 + 23:14:52      23:16:26 + 23:16:27
    23:15:23 + 23:15:24      23:16:58 + 23:16:59

Intervals between pair starts: 32, 32, 33, 30, 32 s. That regularity rules out
interference and distance, which are irregular by nature - this is a *scheduled*
process. Candidates to check next: AP background channel scanning, DHCP lease
renewal, ESP32 WiFi power-save, or a periodic host-side scan.

Confirmed it is a **whole-link blackout, not LiDAR-specific**: the ESP32's own
once-per-second `[DIAG]` messages, which travel on a different UDP port, are
missing for the same seconds (16:14:07 and 16:14:09 absent from the log).
So no amount of LiDAR-path tuning will help. RSSI also swings -57 to -75 within
seconds, and TX failures rose to ~2% while driving (from 0.6% stationary).

## Correction: the `scan_time` root-cause claim was wrong

The installed rf2o implementation does **not** divide displacement by
`LaserScan.scan_time`. It divides by the difference between consecutive scan
header timestamps. The old `scan_time=0.1` metadata was wrong, but the prior
claim that it directly scaled rf2o velocity by 1.57x is retracted. The verified
ROS-side timing findings and replacement implementation are documented below.

### Diagnostic worth reusing

**map->odom correction as a fraction of distance driven** is the single best
health number for this stack. Healthy = small. 84% and 142% both mean the map
is being hauled around as far as the robot travels, i.e. odometry is lying.
Measure it by subscribing to /tf and summing map->odom movement, and to
/odometry/filtered for distance - do NOT use tf2_ros.TransformListener, which
hung and had to be SIGKILLed (it ignores SIGTERM).

### Also fixed this session

- `port_keeper.py` made generic (`--ports`) and now runs in **both** containers.
  udp5005 (Pi webcam) had never been protected and died on essentially every
  ROBOTSERVER restart; `docker port` kept claiming the mapping existed while
  netstat showed no listener. That mismatch is the signature.
- `robot-up.ps1`: udp5005 added to the required-ports check (it was silently
  omitted, so the script printed "done." with the webcam path dead); rviz2
  liveness check changed from `pgrep -f` to `pgrep -x` (the `-f` form matched
  its own shell and ALWAYS reported RUNNING, including while rviz2 was dead
  with "XIO: fatal IO error 2 on X server :125"); ROBOTSERVER kill changed to
  `pkill -x` plus a SIGKILL fallback.
- X sockets change on container restart (VS Code reconnects and creates a NEW
  one, e.g. :125 -> :126, :57 -> :58). Always re-probe for a LIVE socket;
  a process handed a dead one exits immediately.

## Session 7 — ROS2 sensor/odometry code audit (2026-08-06/07)

### rf2o timing correction

The installed source was inspected directly:

    /ros2_ws/src/rf2o_laser_odometry/src/CLaserOdometry2D.cpp
    time_inc_sec = (current_scan_time - last_odom_time).seconds();
    lin_speed = acu_trans(0,2) / time_inc_sec;

`current_scan_time` is copied from `last_scan.header.stamp` in
`CLaserOdometry2DNode.cpp`. Header timestamp quality—not `msg.scan_time`—controls
rf2o velocity in this build.

### Confirmed ROS bridge defects and fixes

The live code was compared with LDROBOT's official LD14/LD14P driver. The
following defects were confirmed and corrected:

- Raw LD14P angles are clockwise, but the bridge published them unchanged as
  ROS counterclockwise angles, mirroring every scan. The decoder now performs
  the manufacturer's clockwise-to-right-handed conversion.
- The manufacturer's ranging-center correction was absent. The decoder now
  uses `offset_x=5.9`, `offset_y=-18.975571`, and
  `y=distance*0.11923+offset_y` before angle binning.
- CRC-8 was ignored. Frames now require the LDROBOT CRC-8 polynomial `0x4D`,
  correct header/length, plausible speed, and plausible angular span. A frame
  with a deliberately altered CRC was rejected in a decoder smoke test.
- LaserScan metadata represented 361 positions for a 360-element array. It now
  publishes exactly 360 beams from `-pi` through `pi-one_increment`, uses NaN
  for missing returns, and drops scans with fewer than 200 valid beams.
- The initial partial rotation after bridge startup was published; it is now
  discarded.
- Receive-time stamping could assign multiple drained/batched scans nearly the
  same timestamp (previously measured minimum interval 0.0002 s). Conversely,
  trusting the LD14P timestamp indefinitely let scans drift 2.2 seconds into
  the future, and slam_toolbox repeatedly rejected them for unavailable TF.
- A two-second raw capture established the timestamp behavior: 720 frames
  arrived in 2.025 s while the packet field advanced exactly 3 ms per frame
  (2.157 s total). The bridge now anchors every receive batch to ROS time,
  preserves ordering inside the batch, and learns a sensor-to-ROS scale. The
  live scale settled around 0.943-0.947.
- The IMU bridge published on both ROT and ACC packets, so ACC packets resent
  stale orientation. It now publishes only normalized ROT quaternions and
  marks unavailable angular velocity with covariance element zero set to -1.
- Async slam_toolbox had `scan_queue_size=10`; its upstream documentation says
  async mode must use 1. It is now 1. `scan_buffer_size=20` remains as the
  separate historical scan-matching chain. LiDAR min/max are 0.1/8.0 m.
- Launch paths depended on the current directory and bridge processes did not
  respawn. Paths are now absolute, bridges respawn, and SLAM/RViz are delayed
  until sensor and TF producers have started.
- `stop_stack.sh` matched `python3 main.py` rather than the actual
  `python3 /root/code/main.py`. A verified orphan survived restart and created
  a second `/scan` publisher. The pattern is fixed, the orphan was removed, and
  `robot-up.ps1` now requires exactly one process for each ROS stack owner.
- The X-display probe accepted stale sockets. It now performs an X11 setup
  handshake. RViz was verified running on the live display `:127`.

Official implementation references:

- https://github.com/ldrobotSensorTeam/ldlidar_sl_ros
- https://github.com/ldrobotSensorTeam/ldlidar_sl_ros/blob/master/ldlidar_driver/src/sl_transform.cpp
- https://github.com/ldrobotSensorTeam/ldlidar_sl_ros/blob/master/src/publish_node/main.cpp
- https://github.com/SteveMacenski/slam_toolbox

### Stationary acceptance test after all fixes

Two-minute test on 2026-08-07 with exactly one owner at every process/topic
layer:

    /scan:               721 messages, 6.01 Hz
      fill:              mean 346.3/360, min 337, max 354
      header intervals:  min 0.1301 s, max 0.9787 s
      arrival - stamp:   min +0.1375 s, max +1.0012 s
      non-monotonic:     0
    /imu/data:           6262 messages, 52.18 Hz
      yaw range:         0.0349 deg; max step 0.0116 deg
    /odom_rf2o:          max stationary speed 0.0033 m/s
      net drift:         0.0004 m; max position step 0.0005 m
    /odometry/filtered:  max stationary speed 0.0037 m/s
      net drift:         0.0013 m; max position step 0.0021 m
    slam TF-filter drops: 0

This confirms the stationary pipeline and timing protections stayed healthy
across multiple recurring approximately one-second WiFi pauses. It does not
yet prove map geometry or loop closure while moving. The next validation is a
controlled driving loop while simultaneously measuring scan age, rf2o versus
EKF velocity, pose jumps, map-to-odom corrections, and real loop-closure events.

Still physically unverified: both static transforms are zero
(`base_link->base_laser` and `base_link->imu_link`). Measured mounting offsets
and yaw must replace zero if either sensor is not truly coincident and aligned
with `base_link`.

### Confirmed duplicate-stack regression after a manual restart

On 2026-08-07, before the robot moved, RViz repeatedly jumped between poses.
ROS endpoint inspection showed exactly two publishers for each of `/scan`,
`/imu/data`, `/odom_rf2o`, and `/odometry/filtered`, and process inspection
showed two complete launch trees. The older tree began at 00:59:18 (PID 14537)
and the newer tree began at 01:24:52 (PID 33903). The diagnostic observed the
stationary output alternating by about 0.253 m while `map->odom` alternated by
about 0.159 m and 3.8 degrees. This proves the visible jump was two independent
stacks publishing the same topic and TF names, not ordinary sensor noise.

`robot-up.ps1` successfully stopped both trees and restarted exactly one owner
for every bridge, odometry, EKF, and SLAM process. In addition,
`launch_all.py` now holds a nonblocking Linux file lock at
`/tmp/robot_ros_stack.lock` for the launch process lifetime. A deliberate
second `ros2 launch launch_all.py` attempt was rejected with:

    ROS2 robot stack is already running (launch PID 37895).

The rejected launch created no additional processes or publishers. The lock is
released automatically by the kernel when the owning launch process exits or
the container restarts, so a stale lock file cannot permanently block startup.

After the clean singleton restart, a 60-second stationary acceptance test had:

    topic publishers:       exactly 1 per sensor/odometry topic
    IMU yaw jumps:          0 (range 0.0244 deg; max step 0.0118 deg)
    EKF pose jumps:         0
    map->odom corrections:  0
    filtered net drift:     0.0017 m
    filtered yaw range:     0.027 deg
    LiDAR:                  6.00 Hz, mean 347.8/360 valid beams

This confirms the stationary axis jump is fixed. Shared IMU/LiDAR pauses of
roughly 0.8-1.1 seconds still occurred periodically and recovered without a
pose jump during this stationary test; their effect while moving remains part
of the controlled driving validation.

### Moving loop acceptance run and remaining late-run failure

A 601.5-second loop drive on 2026-08-07 produced a mostly coherent map and the
operator reported a large improvement. The final diagnostic totals were:

    estimated travel:       48.68 m
    pose graph:             91 nodes, 126 id-0 edge segments
    map:                    12.7 x 11.1 m, 19,572 known cells
    LiDAR:                  3,565 scans, 5.93 Hz
    IMU:                    30,103 messages, 50.05 Hz
    sustained speed splits: 1
    EKF pose jumps:         8
    map->odom corrections: 38, maximum 1.807 m

The operator observed one late failure where a revisited hallway was drawn as
a slightly offset second hallway. A matching pipeline anomaly occurred at
01:47:20-01:47:22:

- The monitor measured rf2o speed at 0.10 m/s while the EKF remained at
  1.36 m/s for longer than 0.5 seconds.
- SLAM then applied seven rapid `map->odom` changes of 0.229-1.279 m and
  1.8-16.2 degrees.
- At 01:47:22 the filtered EKF position changed 2.097 m in one 0.033-second
  output interval.
- The raw rf2o absolute position did not teleport during that interval. Its
  logged position remained approximately (0.664, 1.746) from 01:47:21 through
  01:47:23. The EKF fuses rf2o twist only, not this absolute position.

This is direct evidence that the bad projection was accompanied by a filtered
odometry failure, rather than only a missed loop-closure opportunity. It is a
reasonable inference that scans integrated during/after the 2.1 m filtered
pose discontinuity produced the offset hallway and that SLAM's later graph
corrections did not completely merge it. A bag containing the exact historical
twist samples is still required to prove whether the EKF first accepted a bad
rf2o velocity or rejected a legitimate deceleration and temporarily retained
the old velocity.

The rf2o publisher source and a live `/odom_rf2o` sample both confirm that all
36 twist-covariance values are zero. The EKF fuses `vx` and the nonholonomic
zero `vy` from this message with `odom0_twist_rejection_threshold=5.0`.
Therefore its measurement confidence and rejection gate are not based on a
real estimate of rf2o uncertainty. This is the leading code/configuration
issue for the next iteration; realistic covariance plus explicit
speed/acceleration validation is safer than simply widening the rejection
threshold and allowing arbitrary spikes.

The run also contained 42 LiDAR gaps (maximum 1.672 s) and 39 IMU gaps
(maximum 0.833 s), generally shared and recurring roughly every 31 seconds.
Both streams recovered and there was no sustained packet corruption, but these
gaps reduce scan overlap and remain a separate contributor to weak matching.
They are not yet proven to be the direct trigger for the 01:47:20 event.

## Session 7 — 2026-08-12 — RViz launch failure and missing IMU mapping

The ROS launch initially appeared to produce no map because RViz exited with
`could not connect to display`. VS Code's Remote Containers log showed that its
first attachment skipped X11 forwarding because the Windows-side `DISPLAY` was
not set. A later attachment created a working proxy at `:132`, but the user's
older terminal retained a blank environment. The ROS launch and sensor nodes
had started; RViz alone had failed, and Ctrl+C then stopped the complete stack.

`launch_all.py` now probes the newest VS Code X11 sockets with a real protocol
setup handshake and sets `DISPLAY` before starting RViz. A regression test
explicitly unset `DISPLAY` before `ros2 launch launch_all.py`; RViz initialized
OpenGL and stayed running. Manual display-number management is no longer
required, although a live VS Code container attachment must still exist.

The inaccurate map seen in the same session had a separate confirmed cause.
Windows `netstat` showed UDP 5006 present but UDP 8888 absent. The ESP32's
remote diagnostic counter increased from `isent=118765` to `isent=119964` in
eight seconds while `ifail` stayed at 1224, proving the BNO085 was producing
events and the ESP32 was successfully handing IMU datagrams to WiFi. Inside
ROS, `/imu/data` produced no messages and the EKF yaw remained fixed at zero.
The packets were therefore lost at the Windows-to-Docker UDP forwarding layer.

Restarting `robot_brain` restored both host mappings. The clean restart then
measured approximately 50 Hz on `/imu/data`, 5.9 Hz on `/scan`, exactly one
publisher for each sensor and odometry topic, matching IMU/EKF orientations,
and a live `/map`. `robot-up.ps1` now checks the real Windows sockets before
startup and automatically restarts only the container whose required mapping
is missing. Today's map was discarded because it was built without yaw and is
not valid evidence for changing slam_toolbox loop-closure thresholds.

### ESP32 transmit saturation and corrected sensor rates

After UDP 8888 was restored, synchronized ESP32 and ROS measurements found a
second, independent problem. At RSSI values around -72 to -80 dBm, 25-50% of
the ESP32's `Udp.endPacket()` calls failed. Because the firmware counted the
return value directly, these losses were confirmed to occur on the ESP32
before the datagrams entered Windows or Docker. ROS consequently received only
about 2-3 LiDAR scans per second and incomplete rotations.

The firmware now sends only the IMU quantity used by the EKF, the 6-axis game
rotation vector, at 50 Hz. Unused linear-acceleration reports were removed,
LiDAR batches were increased to 20 frames (940 bytes, below the Ethernet MTU),
and Wi-Fi power saving was disabled. A fixed OTA callback port (3233) was also
added to `deploy-esp32.ps1` because random callback ports sometimes fell in a
Windows excluded range and produced `WinError 10013`.

The deployed firmware was verified from its live remote counters. Over an
eight-second sample on 2026-08-12, it sent approximately 17 LiDAR datagrams per
second and exactly 50 IMU datagrams per second with zero new send failures;
RSSI was -66 to -70 dBm. ROS measured `/scan` at 5.89 Hz and `/imu/data` at
48.85 Hz. The LiDAR bridge reported roughly 341-350 valid beams per 360-beam
scan. These measurements confirm healthy stationary transport; moving loop
closure still requires a new drive on the fresh map.

### ROBOTSERVER duplicate instance and manual-control races

Manual motor controls failed while two separate `ROBOTSERVER` processes were
running. The ESP32 handshake was accepted by one copy while the SDL controller
belonged to the other, splitting connection and keyboard state. `main.cpp` now
holds an exclusive `/tmp/robot_server.lock` for the lifetime of the process.
A deliberate second launch exited with code 1 and printed `ROBOTSERVER is
already running`; exactly one server remained.

Two additional C++ races were corrected:

- `WorkerManager` captured its range-loop variable by reference in each worker
  thread. It now captures the `shared_ptr` by value, so every configured worker
  reliably starts exactly once.
- `SDLWorker` reused one mutable `SensorData` object for key-down and key-up.
  A quick press could mutate the queued FORWARD command into STOP before the
  control thread consumed it. Each keyboard event now queues a fresh snapshot
  of the current key state.

After rebuilding and restarting, the one active server created its SDL
controller on the probed live X display and logged a successful handshake with
ESP32 `192.168.4.80`. TCP 5005 and UDP 5005 were both present. Physical motor
motion is intentionally left for an operator key test; diagnostics do not move
the robot unattended.

`RemoteWebcamProviderWorker` also enables `SO_REUSEADDR`, allowing the temporary
UDP port keeper to protect webcam port 5005 while ROBOTSERVER is restarted.
The normal unattended entry point remains `robot-tools/robot-up.ps1`; it now
restores missing Docker mappings, discovers live X displays, prevents duplicate
stacks, and verifies the resulting processes and sensor streams.

### Motor freeze and map corruption during BNO085 failure

On 2026-08-12, holding a manual movement key drove for only a few seconds and
then stopped while the map deteriorated. This was not an SDL key-repeat fault.
The server log showed successful `MOTOR, FORWARD` datagrams, while the ESP32
remote diagnostics repeatedly followed this sequence:

    [IMU] sensor reset itself - re-enabling reports
    isent=0 while LiDAR continued
    [IMU] no events for 3s - reinitialising I2C
    all diagnostic counters reset about 3-4 seconds later

ROS simultaneously reported `imu: NO UDP packets arriving on 8888`. The cycle
repeated every 6-7 seconds. Inspection confirmed the counter variables are only
initialized at boot, so the resets proved the entire ESP32 was restarting. A
board restart clears motor PWM, explaining why a held key appeared to freeze.
SLAM still received LiDAR without IMU yaw, explaining the map corruption.

The trigger was the firmware's silent-IMU fallback calling
`Wire.begin()`/`bno08x.begin_I2C()` from the main loop. With the BNO085 latched
silent, this call blocked until the ESP32 watchdog restarted the board. The
fallback now reports `[IMU] no events - power-cycle sensor` without re-entering
`begin_I2C()`, and boot logging includes `esp_reset_reason()`. The corrected
firmware was deployed over OTA. A subsequent 10-second test showed continuously
increasing LiDAR counters (2549 to 2700), no board resets, and zero LiDAR send
failures. The BNO085 remained silent, so removing power from the ESP32/BNO085
is still required to restore IMU events; restarting Docker cannot do that.

To protect the live map, `/slam_toolbox/pause_new_measurements` was enabled
without stopping ROS, RViz, or slam_toolbox. The current graph was serialized
before further work to:

    /root/code/maps/pre_imu_failure_20260812_1525.posegraph
    /root/code/maps/pre_imu_failure_20260812_1525.data

After the physical sensor power cycle, verify `isent` increases and ROS receives
approximately 50 Hz on `/imu/data` before toggling the pause service to resume
new scan insertion.

### Fault containment added after the repeated IMU outage

The same BNO085 fault remained after an OTA upload successfully rebooted the
ESP32. After that reboot, LiDAR restarted and sent without failures, but the
IMU counter remained at `isent=0` and the firmware repeatedly reported
`[IMU] no events - power-cycle sensor`. This separates the failure from ROS,
Docker, and the ESP32 process: the powered BNO085 itself was still silent.

The Adafruit driver inspection confirmed that the firmware constructs
`Adafruit_BNO08x` without a reset GPIO. In that configuration the driver's
`hardwareReset()` function does nothing. A true sensor power cycle is therefore
the only confirmed recovery with the current wiring. Connecting the BNO085 RST
pin to a free ESP32 GPIO would allow a future watchdog to reset only the sensor.

`lidar_node.py` now subscribes to `/imu/data` and withholds completed LaserScan
messages whenever IMU data is older than 500 ms. A deliberate test suspended
`imu_bridge.py`: LiDAR datagrams and decoded frames continued, while the bridge
logged `IMU data stale; withholding LiDAR scans to protect SLAM`, reported
`scans=0`, and increased `imu_gate`. Resuming the IMU process produced
`IMU data restored; publishing LiDAR scans` and scan output resumed. This gate
does not repair the sensor; it prevents a partial sensor failure from silently
damaging the map.

Do not suspend the UDP receiver as a routine gate test. During this deliberate
test, Docker's UDP 8888 path stopped delivering afterward even though Windows
and `docker port` still showed the published mapping. A full `robot_brain`
container restart remains the confirmed way to recreate that forwarding path.
The startup script now reports an explicit unhealthy state if bridge counters
show missing IMU data or an active IMU gate, and retries RViz on the live X11
display if VS Code replaces its display socket during reconnect.

### BNO085 report-layer isolation and Pi camera recovery

After a complete robot power disconnection and reconnection, the BNO085 still
produced no game-rotation events. Added remote startup diagnostics established:

    bno=1 report=1 i2c4a=0 i2c4b=2 isent=0

The BNO085 was identified successfully, acknowledged at I2C address `0x4a`,
and accepted the game-rotation configuration, but emitted no quaternion events.
Independent report tests then found that accelerometer events increased at
about 10 Hz and raw-gyroscope events increased at exactly 50 Hz. Calibrated
gyroscope, game-rotation, and gyro-integrated quaternion reports produced zero
events. This confirms that the physical accelerometer, physical gyro, and I2C
connection work; the failed layer is the BNO085 calibrated/fusion report path,
before ESP32 UDP transmission and before ROS.

The SH-2 `sh2_reinitialize()` sensor-hub reset was tested once. It blocked the
ESP32 on this I2C setup, stopping remote logs and both sensor streams. Do not
use that method in the runtime watchdog. The local firmware source was restored
to the normal `SH2_GAME_ROTATION_VECTOR` configuration and compiles, but the
currently running ESP32 requires a physical/USB recovery flash because it is
unreachable after the blocking test. A robust hardware fix is to connect the
BNO085 active-low RST pin to a free ESP32 GPIO so a watchdog can reset only the
sensor. Changing the BNO085 transport away from its problematic I2C mode should
also be evaluated if faults continue.

The Raspberry Pi was independently reachable and reported
`vcgencmd get_throttled=0x0`, meaning no under-voltage or throttling flags were
recorded during that boot. The camera outage occurred because no
`webcam_streamer.py` process was running. A user systemd service named
`robot-webcam.service` is now enabled with restart-on-failure and user lingering,
so it survives SSH/VNC logout and starts at boot. Webcam payloads were increased
from 100 to 1400 bytes, reducing each JPEG from hundreds of UDP datagrams to
roughly 9-12. The service remained active and 1410-byte packets were observed
inside `my-robot-server` on UDP 5005. No ROS restart or map reset was involved.

The normal game-rotation firmware was subsequently flashed over USB on COM3.
This clean startup restored the BNO085 fusion output. In a 15-second soak,
`isent` increased from 3108 to 3858 at exactly 50 events per second, and neither
the IMU nor LiDAR send-failure counter increased. After a full container and
stack restart, ROS measured 48-50 quaternion messages per second and 5-6
complete LiDAR scans per second with `imu_gate=0`, one instance of every ROS
process, RViz running, and a successful ESP32/ROBOTSERVER handshake. The fusion
failure is recovered for this run, but its underlying trigger is not yet proven
and should not be described as permanently eliminated. The ROS freshness gate
provides permanent map protection if it recurs.

After that restart the Pi stopped answering SSH and no webcam datagrams reached
UDP 5005. This is a separate current Pi availability/power/Wi-Fi problem; it did
not affect the confirmed healthy IMU/LiDAR/SLAM pipeline. The enabled webcam
service will start automatically when the Pi is online again.
