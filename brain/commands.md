# Command cheat sheet for this investigation

Run from `/root/code` (launch files resolve `ekf.yaml` relative to cwd).

## Launch the stack
```
ros2 launch launch_all.py
```
Starts: imu_bridge.py, main.py (lidar), 2x static_transform_publisher, rf2o
odometry, ekf_node, slam_toolbox, rviz2.

## Check what's already running (before launching, to avoid port conflicts)
```
ps aux | grep -E 'imu_bridge|main.py|ekf_node|rviz2|slam_toolbox|rf2o' | grep -v grep
```

## Topic-level checks

**Use `topic echo --once`, NOT `topic hz`, for verification — see the gotcha
below. `topic hz` gave false negatives (looked like zero messages) on data
that was actually flowing correctly, confirmed multiple times in Session 2.**

```
ros2 topic list                         # is /imu/data even advertised?
ros2 topic echo /imu/data --once        # one real sample — trust this over hz
ros2 topic echo /scan --once
ros2 topic echo /odometry/filtered --once   # confirms EKF is fusing IMU (compare orientation to /imu/data's)
```

## TF tree (EKF needs base_link<->imu_link to fuse IMU)
```
ros2 run tf2_ros tf2_echo base_link imu_link
```

## Node/diagnostics
```
ros2 node list
ros2 topic echo /diagnostics --once     # ekf.yaml has print_diagnostics: true
```

## From a Windows-host session (not inside the container)

Commands above assume you're inside the container already (e.g. via the VM
session). From a Windows host session instead, prefix with `docker exec
robot_brain`, e.g. `docker exec robot_brain ros2 topic list`. Full Windows-side
command reference (docker/netstat/firewall/WSL2 checks) is in
`windows-docker-networking.md`. Editing files in this `brain/` directory from
a Windows-host session requires round-tripping through `docker cp` since
`/root/code` isn't bind-mounted — see that file's last section for the exact
commands.

**Gotcha:** `docker exec robot_brain sh -c "source ..."` fails with
`sh: 1: source: not found` — `sh` doesn't have `source` as a builtin, only
bash does. Always use `docker exec robot_brain bash -c "source
/opt/ros/humble/setup.bash && source /ros2_ws/install/setup.bash && ..."`.

**Git Bash path-translation gotcha:** if running `docker exec` from Git
Bash on Windows, arguments like `/root/code/...` can get silently rewritten
into Windows paths (e.g. `C:/Program Files/Git/root/code/...`) before Docker
ever sees them, causing `No such file or directory` errors that look like a
container-side problem but aren't. Fix: prefix the command with
`MSYS_NO_PATHCONV=1`.

## Diagnosing "container claims a port is published but no data arrives"
Confirmed working recipe (Session 4, 2026-08-01) for a real port-forwarding
failure between Windows and a container:
```powershell
# 1. Compare what Docker THINKS is published vs what Windows actually has bound
docker port robot_brain                       # Docker's own claim
Get-NetUDPEndpoint | Where-Object { $_.LocalPort -eq <port> }   # ground truth
```
If a port is missing from `Get-NetUDPEndpoint` but present in `docker port`,
Windows has no listener to catch/forward packets on that port — but that
alone doesn't prove packets aren't arriving, only that nothing would catch
them if they did. To check the sender side directly (needs an
Administrator/elevated terminal):
```powershell
pktmon filter remove
pktmon filter add -p <port>
pktmon start --etw -f "$env:TEMP\pktmon_<port>.etl" --capture
# wait ~15s while the sender is active
pktmon stop
pktmon format "$env:TEMP\pktmon_<port>.etl" -o "$env:TEMP\pktmon_<port>.txt"
```
Then search the formatted text for the destination port — pktmon's format
uses tcpdump-style summary lines like
`src_ip.src_port > dst_ip.dst_port: UDP, length N`, NOT field names like
`DestinationPort =` (searching for that returns nothing even when packets
are present — confirmed this the hard way). E.g. `grep "192.168.4.78.*5006.*UDP"`.
Remember `pktmon filter remove` when done to stop capturing everything.

## Gotcha: `ros2 topic hz` gives false negatives in non-TTY contexts — use `topic echo --once` instead

**Confirmed in Session 2: don't trust `ros2 topic hz` when run non-interactively
(e.g. `docker exec ... bash -c "timeout N ros2 topic hz ..."`).** It showed
zero messages on `/imu/data` and `/scan` repeatedly, even while data was
provably flowing correctly (confirmed with `nc` and with `topic echo --once`
at the exact same time). Best explanation: `ros2 topic hz` is itself an
rclpy/Python tool; wrapping it in `timeout N` kills it with SIGTERM, and
Python fully-buffers stdout when not attached to a TTY — the same underlying
issue as the `imu_bridge.py print()` gotcha below. It may have been computing
a real rate the whole time and simply never got to flush/print it before
being killed. **Use `ros2 topic echo /topic --once` for verification instead**
— it exits normally after one message, so it doesn't hit this trap.

Related, separate gotcha: don't trust a UDP socket's `rx_queue` field (from
`/proc/net/udp` or `ss`) as evidence that "nothing has arrived." It only
shows bytes *currently buffered and unread* — a socket being read immediately
by a healthy process will always show `rx_queue=0`, indistinguishable from
one receiving nothing. Use `nc -ul <port>` (after freeing the port) or
`topic echo --once` to actually confirm receipt.

## Gotcha: imu_bridge.py's print() debug lines
`imu_bridge.py` uses plain `print("ROT -> ...")` / `print("ACC -> ...")` for
debug output. When launched via `ros2 launch` with output redirected to a file
(not a TTY), Python fully-buffers stdout, so these lines may not appear until
the process exits or the buffer fills. If you really need the print output
live, re-run with:
```
python3 -u imu_bridge.py
```
