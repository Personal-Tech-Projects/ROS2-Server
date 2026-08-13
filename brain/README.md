# /brain — persistent working memory for this investigation

This directory is scratch/working memory for whichever agent (or human) is
investigating the SLAM/IMU pipeline. Feel free to edit any file here directly.

## Files

- `docker-port-forwarding-failure.md` — **RUNBOOK for a RECURRING bug.** If sensor
  data stops with no code change (no map, no `/scan`, no `/imu/data`), read this
  FIRST — 10-second check plus a confirmed fix. Occurred 2026-08-01 and 2026-08-05.
- `system-overview.md` — how data actually flows: ESP32 firmware -> network -> ROS2 nodes -> EKF -> SLAM.
  Read this first if you're new to the investigation.
- `investigation-log.md` — dated, append-only log of what was tried and what was found.
  Add a new entry at the top each session; don't rewrite old entries, just add corrections inline if something turns out wrong.
- `commands.md` — cheat sheet of commands used to inspect this system (ros2 topic/node introspection, etc).
- `open-questions.md` — things we don't know yet / need the user to confirm.
- `windows-docker-networking.md` — Docker Desktop / WSL2 / Windows Firewall
  findings from the docker-restart investigation. Written from a session on
  the Windows host itself, not inside this container.
- `esp32-deployment.md` — how to find, edit, compile, flash, and verify the
  ESP32 firmware from the Windows host (toolchain paths, board FQBN, and a
  proven technique for confirming a deploy actually took effect). Read this
  before touching the ESP32 firmware again.
- `todo.md` — actionable code cleanup/refactor tasks the user has asked for
  (not investigation unknowns — those are in `open-questions.md`). Check here
  before starting a session to see if there's a pending task to pick up.

## Current headline status (keep this updated)

**RESOLVED — confirmed working end-to-end, 2026-08-01 Session 2.** The
original question this whole investigation started with (does IMU data
reach the EKF and contribute to the SLAM map?) is answered **YES**, verified
live via `ros2 topic echo` on `/imu/data`, `/scan`, and `/odometry/filtered`
(the EKF's orientation output tracks the raw IMU orientation closely).

**Root cause chain (see `investigation-log.md` Session 2 for full detail):**
a **third container, `my-robot-server`**, which answers the ESP32's TCP:5005
handshake for motor control, had exited 4 hours earlier (it's an interactive
`/bin/bash` session with no restart policy — dies whenever its terminal
disconnects). Per the ESP32 firmware's original design, a failed handshake
meant the ESP32 **never sent ANY sensor data at all** (IMU or LiDAR) —
confirmed via a zero-byte Windows-host packet capture.

**Fix deployed and verified:** rewrote the ESP32 firmware's `loop()` so
IMU/LiDAR sending no longer waits on the TCP:5005 handshake (motor control
still correctly depends on it — only sensor streaming was decoupled).
Compiled, flashed, verified the deploy pipeline itself with a temporary
serial marker (`esp32-deployment.md` step 8), then reflashed the clean
version. Confirmed via packet capture + raw `nc` + live topic echoes that
real sensor data now flows all the way to the EKF, with `my-robot-server`
still down — proving the whole point of the fix.

**Important correction along the way:** a `docker restart robot_brain` did
NOT fix a subsequent false alarm — `ros2 topic hz` showed zero messages even
after restart, which looked like the WSL2-NAT-forwarding theory from earlier
in Session 2 being confirmed. It wasn't. `ros2 topic hz` itself was giving
false negatives (buffering/timeout issue, not a real data problem) — see
`commands.md` gotchas section. **Don't trust `ros2 topic hz` for
verification in this project; use `ros2 topic echo --once`.**

Two open items remain, neither blocking the core question above:

1. **`my-robot-server`'s reliability:** still needs to be turned into a
   proper background service (not an interactive shell someone has to keep
   a terminal attached to) so it survives disconnects/reboots. Needed for
   motor control / manual override to work reliably — not addressed yet.
2. **Housekeeping:** the currently-running `imu_bridge.py`/`main.py` were
   started manually (`python3 -u ... > brain/*_debug.log`) for debugging,
   not via the normal `ros2 launch launch_all.py`. Functionally equivalent,
   but if you want the standard launch running instead, stop these two and
   relaunch normally (see `commands.md`).
