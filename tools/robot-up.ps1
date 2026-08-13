<#
Bring the whole robot stack up, unattended.

    .\robot-up.ps1            # restart the servers (ROS2 stack + ROBOTSERVER)
    .\robot-up.ps1 -Full      # also restart the containers (restores dead port mappings)
    .\robot-up.ps1 -Status    # report only, change nothing

Everything is discovered at runtime - no hardcoded DISPLAY, no hardcoded IPs.
The ESP32 can stay powered on throughout: a port keeper holds UDP 5006/8888
across the restart window so the Docker mappings cannot be destroyed.
#>
param(
    [switch]$Full,
    [switch]$Status
)

$ErrorActionPreference = "Continue"
. "$PSScriptRoot\robot-env.ps1"

function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }

# --- DISPLAY discovery -------------------------------------------------------
# X sockets accumulate in /tmp/.X11-unix (one per VS Code X-forwarding session).
# The newest is the live one. Hardcoding it breaks on the next session.
# Picking the newest socket is NOT enough: stale sockets from ended sessions
# linger and some still accept connections. Probe each one newest-first and
# take the first that a client can actually connect to.
function Get-ContainerDisplay($container) {
    $probe = @'
import os, socket
d = "/tmp/.X11-unix"
socks = sorted([f for f in os.listdir(d) if f.startswith("X") and f[1:].isdigit()],
               key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)
for f in socks[:10]:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(1)
    try:
        s.connect(os.path.join(d, f))
        # A stale VS Code X proxy can still accept() a Unix connection but
        # fail the X11 setup handshake, which makes Qt/RViz exit immediately.
        # Probe the protocol, not only the socket. This is an unauthenticated
        # little-endian X11 SetupRequest (protocol 11.0).
        s.sendall(b'l\x00\x0b\x00\x00\x00\x00\x00\x00\x00\x00\x00')
        reply = s.recv(8)
        if reply and reply[0] == 1:
            print(f[1:]); break
    except Exception:
        pass
    finally:
        s.close()
'@
    $n = $probe | docker exec -i $container python3 - 2>$null
    if ($n) { return ":$($n.Trim())" }
    return $null
}

function Get-Mappings {
    $u = netstat -ano -p UDP
    $t = netstat -ano -p TCP
    [pscustomobject]@{
        udp5005 = [bool]($u | Select-String ":5005\s")
        udp5006 = [bool]($u | Select-String ":5006\s")
        udp8888 = [bool]($u | Select-String ":8888\s")
        tcp5005 = [bool]($t | Select-String ":5005\s.*LISTENING")
    }
}

function Show-Status {
    Say "`n=== containers ===" Cyan
    docker ps --format "  {{.Names}}  {{.Status}}"
    Say "`n=== processes ===" Cyan
    docker exec robot_brain bash -lc 'ps -eo pid,etime,cmd | grep -E "launch_all|imu_bridge|main\.py|ekf_node|rf2o|slam_toolbox|rviz2" | grep -v grep' 2>$null
    docker exec my-robot-server bash -lc 'ps -eo pid,etime,cmd | grep ROBOTSERVER | grep -v grep' 2>$null
    Say "`n=== port mappings ===" Cyan
    $m = Get-Mappings
    foreach ($k in "tcp5005", "udp5005", "udp5006", "udp8888") {
        if ($m.$k) { Say "  $k : MAPPED" Green } else { Say "  $k : GONE" Red }
    }
    Say "`n=== ESP32 ===" Cyan
    $ip = Get-Esp32Ip
    if ($ip) { Say "  at $ip" Green } else { Say "  NOT FOUND (powered off?)" Red }
}

if ($Status) { Show-Status; exit 0 }

# --- 1. optional container restart ------------------------------------------
if ($Full) {
    Say "==> restarting containers (this recreates the UDP port mappings)" Cyan
    docker restart robot_brain my-robot-server | Out-Null
    Start-Sleep -Seconds 4
} else {
    $m = Get-Mappings
    $restart = @()
    if (-not $m.udp5006 -or -not $m.udp8888) { $restart += "robot_brain" }
    if (-not $m.tcp5005 -or -not $m.udp5005) { $restart += "my-robot-server" }
    if ($restart.Count -gt 0) {
        Say "==> restoring missing Docker port mappings: $($restart -join ', ')" Yellow
        docker restart $restart | Out-Null
        Start-Sleep -Seconds 4
    }
}

# --- 2. hold the sensor ports before anything else -------------------------
# A keeper is needed in BOTH containers, because they own different ports:
#   robot_brain      udp 5006 (lidar) + 8888 (imu)
#   my-robot-server  udp 5005 (Pi webcam video)
# udp5005 had no keeper until 2026-08-06 and died on essentially every
# ROBOTSERVER restart, silently discarding the Pi's video while `docker port`
# still claimed the mapping was fine.
Say "==> starting port keepers (robot_brain 5006/8888, my-robot-server 5005)" Cyan
# Anchor the match to the actual Python process. A broad `pkill -f port_keeper`
# also matches this `bash -lc ...port_keeper...` command and kills the shell
# before it can launch the replacement keeper.
docker exec robot_brain bash -lc "pkill -f '^python3 /root/code/port_keeper.py' >/dev/null 2>&1; setsid nohup python3 /root/code/port_keeper.py --seconds 120 --ports 5006,8888 > /tmp/port_keeper.log 2>&1 < /dev/null &" 2>$null
docker exec my-robot-server bash -lc "pkill -f '^python3 /root/code/port_keeper.py' >/dev/null 2>&1; setsid nohup python3 /root/code/port_keeper.py --seconds 120 --ports 5005 > /tmp/port_keeper.log 2>&1 < /dev/null &" 2>$null
Start-Sleep -Seconds 2

# --- 3. discover DISPLAY ----------------------------------------------------
$dBrain  = Get-ContainerDisplay robot_brain
$dServer = Get-ContainerDisplay my-robot-server
for ($attempt = 0; $attempt -lt 15 -and (-not $dBrain -or -not $dServer); $attempt++) {
    Start-Sleep -Seconds 1
    if (-not $dBrain)  { $dBrain  = Get-ContainerDisplay robot_brain }
    if (-not $dServer) { $dServer = Get-ContainerDisplay my-robot-server }
}
Say "    DISPLAY: robot_brain=$dBrain  my-robot-server=$dServer" DarkGray
if (-not $dBrain)  { Say "    WARNING: no X socket in robot_brain - rviz2 will not open" Yellow }
if (-not $dServer) { Say "    WARNING: no X socket in my-robot-server - the SDL window will not open" Yellow }

# --- 4. (re)start the ROS2 stack -------------------------------------------
Say "==> stopping old ROS2 stack" Cyan
# Must VERIFY the old stack is gone, not just fire pkill and hope. Killing the
# launch first can orphan its children, and a surviving ekf_node publishes a
# second, conflicting odom->base_link transform - slam_toolbox then rejects
# every scan with "timestamp is earlier than all the data in the transform
# cache" and no map is ever built. Seen on 2026-08-05 with two ekf_nodes alive.
# NOTE: run this from a FILE in the container, not by piping a here-string into
# `docker exec -i`. That pipe fails silently through PowerShell, which left the
# old stack running and produced two of every node.
$left = docker exec robot_brain bash /root/code/stop_stack.sh
Say "    $left" DarkGray

Say "==> starting ROS2 stack" Cyan
docker exec robot_brain bash -lc "cd /root/code && export DISPLAY=$dBrain && source /opt/ros/humble/setup.bash && source /ros2_ws/install/setup.bash 2>/dev/null; setsid nohup ros2 launch launch_all.py > /tmp/launch_all.log 2>&1 < /dev/null &" 2>$null

# --- 5. (re)start ROBOTSERVER ----------------------------------------------
# stdin must stay OPEN but silent: main.cpp blocks on `std::cin >> input`, and
# with stdin on /dev/null that returns EOF instantly and busy-spins a core.
Say "==> restarting ROBOTSERVER" Cyan
# MUST be `pkill -x` (exact process NAME), not `pkill -f` (full command line).
# `-f ROBOTSERVER` matches this very shell, whose own command line contains the
# string, so the shell SIGTERMs itself before `sleep 1` runs and the real
# ROBOTSERVER survives - then step 5 starts a second one. Two servers both bind
# port 5005 and the ESP32's handshake lands on whichever won the race.
# Seen 2026-08-06 (exit code 143 = SIGTERM on the docker exec).
docker exec my-robot-server bash -lc 'pkill -x ROBOTSERVER; sleep 1' 2>$null
$still = docker exec my-robot-server bash -lc 'pgrep -x ROBOTSERVER | wc -l' 2>$null
if ($still -and $still.Trim() -ne "0") {
    Say "    old ROBOTSERVER did not die - forcing" Yellow
    docker exec my-robot-server bash -lc 'pkill -9 -x ROBOTSERVER; sleep 1' 2>$null
}
docker exec my-robot-server bash -lc "cd /root/code/RobotServer && export DISPLAY=$dServer; setsid nohup bash -c 'tail -f /dev/null | ./build/bin/ROBOTSERVER' > /tmp/robotserver.log 2>&1 < /dev/null &" 2>$null

Say "    waiting for nodes to come up..." DarkGray
Start-Sleep -Seconds 12

# VS Code can replace its X11 socket while reconnecting after a container
# restart. Use the live socket found after startup for GUI verification.
$liveBrainDisplay = Get-ContainerDisplay robot_brain
if ($liveBrainDisplay) { $dBrain = $liveBrainDisplay }
$liveServerDisplay = Get-ContainerDisplay my-robot-server
if ($liveServerDisplay) { $dServer = $liveServerDisplay }

# --- 6. release the keepers -------------------------------------------------
docker exec robot_brain bash -lc "pkill -f '^python3 /root/code/port_keeper.py' >/dev/null 2>&1" 2>$null
docker exec my-robot-server bash -lc "pkill -f '^python3 /root/code/port_keeper.py' >/dev/null 2>&1" 2>$null
Say "==> port keepers released" Cyan

# --- 7. verify --------------------------------------------------------------
Say "`n==> verifying" Cyan
$m = Get-Mappings
$bad = @()
# udp5005 (Pi webcam video) MUST be in this list. It was omitted, so on
# 2026-08-06 the script printed "done." while udp5005 was dead and the Pi's
# video went nowhere - the Pi reported streaming fine, because the frames are
# discarded on the Windows side. Note `docker port` still claimed
# "5005/udp -> 0.0.0.0:5005" while netstat showed nothing listening: that
# mismatch is the signature of a dead Docker userland proxy, and only a
# CONTAINER restart (-Full) rebuilds it. Restarting ROBOTSERVER cannot.
foreach ($k in "tcp5005", "udp5005", "udp5006", "udp8888") { if (-not $m.$k) { $bad += $k } }
foreach ($k in "tcp5005", "udp5005", "udp5006", "udp8888") {
    if ($m.$k) { Say "  $k : MAPPED" Green } else { Say "  $k : GONE" Red }
}
if ($bad.Count -gt 0) {
    Say "  !! $($bad -join ', ') missing - rerun with -Full to recreate the containers" Yellow
}

# A healthy ROS graph needs exactly one process for every producer/owner.
# Topic names alone are insufficient: with SO_REUSEADDR, two bridge processes
# can coexist while only one receives UDP, and ROS still reports both nodes.
Say "`n  ROS process ownership:" Cyan
$brainProcesses = @(docker exec robot_brain ps -eo args= 2>$null)
$processChecks = @(
    @{ Name = "launch_all"; Pattern = '^/usr/bin/python3 /opt/ros/humble/bin/ros2 launch launch_all\.py$' },
    @{ Name = "imu_bridge"; Pattern = '^python3 /root/code/imu_bridge\.py$' },
    @{ Name = "lidar_bridge"; Pattern = '^python3 /root/code/main\.py$' },
    @{ Name = "rf2o"; Pattern = '^/ros2_ws/install/rf2o_laser_odometry/.*/rf2o_laser_odometry_node .*' },
    @{ Name = "ekf"; Pattern = '^/opt/ros/humble/lib/robot_localization/ekf_node .*' },
    @{ Name = "slam_toolbox"; Pattern = '^/opt/ros/humble/lib/slam_toolbox/async_slam_toolbox_node .*' }
)
foreach ($check in $processChecks) {
    $count = @($brainProcesses | Where-Object { $_ -match $check.Pattern }).Count
    if ($count -eq 1) { Say "    $($check.Name): 1" Green }
    else { Say "    $($check.Name): $count (expected exactly 1)" Red }
}

# Verify from the bridges' own per-second counters, NOT `ros2 topic echo`.
# The CLI reports SILENT on topics that are demonstrably publishing (same
# unreliability the brain notes flag for `ros2 topic hz`) - it produced a false
# "everything is down" here while /scan was running at 7 Hz with 97% fill.
Say "`n  sensor bridges (from their own counters):" Cyan
$bridge = docker exec robot_brain bash -lc 'grep -e lidar: -e imu: /tmp/launch_all.log | tail -4' 2>$null
if ($bridge) { $bridge | ForEach-Object { "    $_" } } else { Say "    no bridge output yet" Yellow }
$imuLine = docker exec robot_brain bash -lc "grep '\[imu_bridge\]: imu:' /tmp/launch_all.log | tail -1" 2>$null
$lidarLine = docker exec robot_brain bash -lc "grep '\[lidar_bridge\]: lidar:' /tmp/launch_all.log | tail -1" 2>$null
if (-not $imuLine -or $imuLine -match 'NO UDP packets') {
    Say "    HEALTH: IMU DATA MISSING - do not build a map" Red
} elseif ($imuLine -match 'published=([1-9][0-9]*)') {
    Say "    HEALTH: IMU flowing" Green
} else {
    Say "    HEALTH: IMU not yet confirmed" Yellow
}
if ($lidarLine -match 'imu_gate=([1-9][0-9]*)') {
    Say "    HEALTH: LiDAR scan output blocked because IMU is stale" Red
} elseif ($lidarLine -match 'scans=([1-9][0-9]*)') {
    Say "    HEALTH: LiDAR scans flowing" Green
} else {
    Say "    HEALTH: LiDAR scans not yet confirmed" Yellow
}
Say "`n  rviz2:" Cyan
# MUST be `pgrep -x` (process NAME). `pgrep -f rviz2` matches this very shell,
# whose command line contains "rviz2", so it ALWAYS reported RUNNING - on
# 2026-08-06 it printed "rviz2: RUNNING on :125" while rviz2 had already died
# with "XIO: fatal IO error 2 on X server :125" and the user had no viewer.
# Also re-probe the display: X sockets change on container restart (VS Code
# reconnects and creates a NEW one, e.g. :125 -> :126), and rviz2 dies if the
# socket it was handed goes away.
$rv = docker exec robot_brain bash -lc 'pgrep -x rviz2 >/dev/null && echo RUNNING || echo "NOT RUNNING"' 2>$null
if ($rv -notmatch "RUNNING" -and $dBrain) {
    Say "    relaunching on live DISPLAY $dBrain" Yellow
    docker exec robot_brain bash -lc "export DISPLAY=$dBrain; setsid nohup rviz2 > /tmp/rviz-recovery.log 2>&1 < /dev/null &" 2>$null
    Start-Sleep -Seconds 3
    $rv = docker exec robot_brain bash -lc 'pgrep -x rviz2 >/dev/null && echo RUNNING || echo "NOT RUNNING"' 2>$null
}
if ($rv -match "RUNNING") { Say "    RUNNING on $dBrain" Green } else { Say "    NOT RUNNING (no live X11 display)" Yellow }

$map = docker exec robot_brain bash -lc 'source /opt/ros/humble/setup.bash; timeout 20 ros2 topic echo /map --field info 2>/dev/null | grep -e ^width -e ^height | head -2' 2>$null
if ($map) { Say "`n  map:" Cyan; $map | ForEach-Object { "    $_" } }
else { Say "`n  map: not publishing yet (normal for the first few seconds)" Yellow }

Say "`ndone." Green
