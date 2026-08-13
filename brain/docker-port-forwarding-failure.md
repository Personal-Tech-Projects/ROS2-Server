# Docker UDP port-forwarding failure (`robot_brain`) — RECURRING

> **READ REVISION 2 AT THE BOTTOM FIRST.** Two theories appear in this document
> (startup-timing, then idle-reaping). **Both are unsupported** — idle-reaping was
> tested and refuted. The *cause* of a mapping vanishing is undetermined. The
> *recovery procedure* is confirmed and unaffected: see "Practical procedure that
> is confirmed to work".

**This has now happened twice (2026-08-01, 2026-08-05). If sensor data
"stops working" with no code change, check this FIRST — it takes 10 seconds
to rule in or out.**

---

## Symptom

- RViz shows no map / SLAM never builds.
- `/scan` and/or `/imu/data` have a publisher but zero messages.
- The ROS2 stack is fully running, the bridges are alive, sockets are bound
  *inside* the container, and nothing has changed in the code.
- Everything looks healthy from inside the container. That is the trap.

## 10-second diagnosis (run on the WINDOWS host)

```powershell
netstat -ano -p UDP | Select-String ":5006|:8888"
```

| Output | Meaning |
|---|---|
| Two lines, owner PID = `com.docker.backend` | Forwarding is fine — problem is elsewhere |
| One line | That one port is broken (this is what happened 2026-08-01) |
| Nothing | Both are broken (this is what happened 2026-08-05) |

Compare against `my-robot-server`'s port as a control:

```powershell
netstat -ano -p UDP | Select-String ":5005"
```

If 5005 is present but 5006/8888 are not, the daemon is fine and only
`robot_brain`'s mappings failed.

**`docker ps` / `docker port` will lie to you here.** Both cheerfully report
`0.0.0.0:5006->5006/udp` while no host socket exists. Docker's internal
bookkeeping and the actual Windows socket state disagree — that split-brain
IS the bug.

Confirming proof of the split-brain (2026-08-05): trying to start any other
container on those ports is refused by the daemon —

```
Bind for 0.0.0.0:5006 failed: port is already allocated
```

...while Windows has no listener at all. The daemon thinks it's allocated;
the host never got the socket.

## The fix

```powershell
docker restart robot_brain
```

**Confirmed working 2026-08-05.** Ports went from absent to bound by
`com.docker.backend` immediately after the restart.

This kills the ROS2 stack (all nodes are children of the launch running in
the VS Code terminal), so relaunch afterward:

```bash
ros2 launch launch_all.py
```

### Why a restart is the ONLY route (verified, not assumed)

1. Port mappings are programmed **once, at container start**. That is
   literally what the daemon error names: *"failed to set up container
   networking: driver failed programming external connectivity."*
2. Restarting processes *inside* the container does nothing. The mail slot
   is created by Docker **outside** the container, before any of your
   processes exist. `imu_bridge.py` / `main.py` are never the problem.
3. There is no Docker command to add or repair a mapping on a running
   container.
4. You cannot work around it with a relay/sidecar container either — the
   daemon holds 5006/8888 allocated to `robot_brain` and refuses any other
   claim (proven above).

## Why it happens — best theory, NOT proven

`robot_brain` requested its mappings ~11 seconds into Docker Desktop's own
startup, while the WSL networking layer was still assembling. The request
landed on a subsystem that wasn't ready, was dropped, and was never retried.

Timeline from the 2026-08-05 occurrence (local time):

| Time | Event |
|---|---|
| 11:44:46–48 | Docker Desktop + `com.docker.backend` launch |
| 11:44:49–54 | WSL plumbing still spawning (`wslrelay`, `wslhost`, `wsl`) |
| **11:44:58.2** | **`robot_brain` starts → mappings FAILED** |
| **11:44:59.1** | **`my-robot-server` starts → mappings OK** |

**Note the container that started FIRST is the one that broke.** There is no
contention between the containers — they use different ports (5005 vs
5006/8888), so there is nothing to fight over. Any explanation based on
"first one claims the port" is wrong. What matters is timing relative to
**Docker Desktop's initialization**, not relative to the other container.

Both containers have `restartPolicy=no`, so Docker is not auto-starting
them — something external (startup script, VS Code Dev Containers, a
shortcut) launched them the instant Docker Desktop appeared.

**Confidence:** the mechanism being healthy once Docker is settled is
*proven* (see below). The startup-timing *cause* is unproven — Docker's
`com.docker.backend.exe.log` had already rotated to 0 bytes by the time we
looked. Treat it as the leading hypothesis, not fact.

## What is PROVEN (tested 2026-08-05)

Tested with a throwaway container on alternate ports (15006/18888) so the
real containers were never touched:

| Test | Result |
|---|---|
| Can Docker create UDP forwarding while fully initialized? | **Yes** — ports appeared in netstat under `com.docker.backend` immediately |
| End-to-end host→container UDP (sent to `192.168.4.81:18888`) | **All 5 packets arrived inside the container** |
| Claiming the real ports 5006/8888 from another container | **Refused** — `port is already allocated` |
| After `docker restart robot_brain` | Host ports bound; **450 real IMU packets in 12s** from the actual ESP32 |

So: the ports, UDP forwarding, the LAN path, and the bridge code are all
fine. The mapping simply never existed.

## Tests that DO NOT work — do not trust these

These have each burned us at least once:

- **`docker ps` / `docker port`** — reports mappings that do not exist on the
  host. Useless for this bug.
- **Binding the port yourself from PowerShell** (`New-Object
  System.Net.Sockets.UdpClient($port)`) — a successful bind does **NOT**
  prove nothing is listening. Windows permits sharing a UDP port that was
  bound non-exclusively; a bind on 15006/18888 succeeded while
  `com.docker.backend` was demonstrably listening on them. **Use `netstat`,
  not a bind attempt.** (This one fooled us on 2026-08-05.)
- **`/proc/net/udp` `rx_queue` / `drops`** — `rx_queue=0` means "nothing
  buffered *at this instant*," not "nothing ever arrived." A healthy socket
  being read promptly always shows 0. Indistinguishable from a dead one.
  (Fooled us on 2026-08-01.)
- **`ros2 topic hz`** — has reported nothing on topics that were genuinely
  publishing. Use `ros2 topic echo <topic> --once` instead.

### The general lesson (learned twice now)

**Absence of evidence on the receiver is not proof the sender is silent.**
Before concluding "the ESP32 isn't sending," confirm with a direct, lower-
level tool:

- `pktmon` on Windows (needs an **Administrator** terminal) — proved on
  2026-08-01 that packets were physically hitting the NIC:
  `192.168.4.78.4210 > 192.168.4.81.5006: UDP, length 47`
- A raw socket bound inside the container (`nc -ul 5006`, or a small Python
  listener) once the ROS2 stack is stopped so the port is free.

## Prevention

1. **Don't start the containers while Docker Desktop is still coming up.**
   Gate on readiness — `docker info` succeeding is a reasonable check —
   rather than firing the moment the Docker process exists. This is where
   the real fix lives if the timing theory is right.
2. **Always run the netstat check after startup**, before debugging anything
   else. Ideally wire it into whatever script brings the stack up so it warns
   automatically. This failure is completely silent otherwise and looks
   exactly like a code bug.
3. **Structural option, not yet attempted:** Docker Desktop supports host
   networking for Linux containers. That removes the forwarding layer
   entirely, so this failure becomes impossible rather than merely unlikely.
   Bigger change — affects how all three ports are reached — so test it
   deliberately, not mid-debug.

## Occurrence history

| Date | Ports affected | How found | Fix confirmed? |
|---|---|---|---|
| 2026-08-01 | 5006 only (8888 OK) | `Get-NetUDPEndpoint` + `pktmon` capture | Restart proposed, **never actually applied/verified** |
| 2026-08-05 | 5006 **and** 8888 | `netstat` vs. 5005 control | **Yes** — restart fixed it, real IMU data confirmed flowing after |

---

## Related

- `investigation-log.md` — Session 4 (2026-08-01) and Session 5 (2026-08-05)
  entries, full chronological detail.
- `system-overview.md` — "A DIFFERENT, separately-confirmed port-forwarding
  failure" section.
- `windows-docker-networking.md` — background on the Docker Desktop / WSL2
  NAT setup and the userland proxy.

---

# ⚠ MAJOR REVISION (2026-08-05, same session, ~40 min after the above was written)

**The startup-timing theory above is probably WRONG. A better-supported
theory replaces it: idle UDP mappings appear to be reaped at runtime.**

## The observation that broke the old theory

After `docker restart robot_brain` at 20:16:06 UTC, **both** 5006 and 8888
were confirmed bound on the host. Ten and a half minutes later, with no
restart and no config change:

```
UDP    0.0.0.0:5005     ...     42284     ← alive (ESP32 pings every 1s)
UDP    0.0.0.0:8888     ...     42284     ← alive (IMU ~37 Hz)
                                          ← 5006 GONE
```

`docker port robot_brain` still claimed `5006/udp -> 0.0.0.0:5006`.

**A mapping that was verified working disappeared on its own.** So the bug is
not only "fails to be created at container start" — an established mapping
can also vanish at runtime.

## The pattern that fits every observation so far

| Port | Traffic | Outcome |
|---|---|---|
| 5005 | ESP32 pings, 1/s | survives |
| 8888 | IMU, ~37 Hz | survives |
| 5006 | **none** (LiDAR silent) | **reaped within <10.5 min** |

**The ports that carry traffic survive. The idle one dies.**

This also retroactively explains **Session 4 (2026-08-01)** far better than
the startup theory did: 5006 was broken while 8888 was fine — exactly what
idle-reaping predicts if the LiDAR began sending only after the idle window
had already expired. The old theory had no good explanation for why one port
of the same container would fail while its sibling worked.

It equally explains this session's initial state (both ports dead): the
containers started at 11:44:58 and the ESP32 was not necessarily online yet,
so **both** mappings sat idle and both were reaped.

## Confidence and what would confirm it

**Not proven.** The correlation is strong and consistent across three
independent observations, but the reaping mechanism itself has not been
demonstrated, and the exact timeout is only bounded (<10.5 min, lower bound
unknown).

**The test that would confirm it:** restart `robot_brain`, then send a dummy
UDP packet to `127.0.0.1:5006` from the Windows host every ~30s. If 5006 is
still bound well past the window that killed it while idle, idle-reaping is
confirmed and the keepalive is also the fix.

## Practical consequence — read this before debugging the LiDAR

**Fixing the LiDAR alone will not restore the map.** If the LiDAR starts
sending while 5006 has already been reaped, the packets are still discarded
at the host. Both conditions must hold at the same time:

1. The LiDAR is actually transmitting, **and**
2. The 5006 mapping currently exists.

So the correct order is: get the LiDAR sending first, *then* restart
`robot_brain` (or keep 5006 warm), so the mapping is created while traffic
is already flowing and never goes idle.

## Proposed keepalive (untested)

Run on the Windows host, alongside the stack:

```powershell
$u = New-Object System.Net.Sockets.UdpClient
$b = [byte[]](0)
while ($true) {
  [void]$u.Send($b, 1, "127.0.0.1", 5006)
  Start-Sleep -Seconds 30
}
```

Side effect: `lidar_bridge` will log `Packet rejected by decoder` once every
30s. Harmless, and arguably useful as a heartbeat that proves the path is
alive. Do **not** send anything resembling a valid `0x54 0x2C` header — a
malformed packet must never be mistaken for real scan data.

## Outcome of the revision's proposed procedure (same session, confirmed)

**The "restart while traffic is already flowing" ordering was applied and it
worked.** With the ESP32 confirmed transmitting ~250 LiDAR packets/sec (proven
at the source via serial diagnostics, not inferred), `docker restart
robot_brain` recreated both mappings, and a raw listener inside the container
immediately received real data on both ports:

```
LiDAR 5006 packets in 10s: 819   sample: 542c6f0819698900b48900b48900a488
IMU   8888 packets in 10s: 710   sample: b'ACC,-0.0039,0.07'
```

### Correction to the reaping table above

The table lists 5006's traffic as "none (LiDAR silent)". **That was an
assumption, not a measurement** — at the time it was written there was no
source-side evidence, because the ESP32 was not plugged in by USB. It may be
true (the LiDAR's transmission does appear to have changed when USB was
connected) or the LiDAR may have been transmitting all along, in which case
5006 was NOT idle and the reaping theory is wrong.

**So the idle-reaping theory remains the leading explanation but is now less
well-supported than the section above implies.** What is solidly established
regardless of mechanism:

1. A mapping verified as bound **can disappear at runtime**, without a restart
   (5006 did, between 20:16 and 20:26 UTC). This is a fact, independent of why.
2. `docker restart robot_brain` reliably restores it.
3. Restarting **while the sensor is actually transmitting** produced a working
   result. Whether that ordering is strictly necessary is unproven, but it
   costs nothing and removes one variable.

### Practical procedure that is confirmed to work

1. Verify the sensor is genuinely transmitting **at the source** — for the
   ESP32, flash the `[LIDAR-DIAG]` instrumentation and read COM3. Do not infer
   it from receiver-side silence.
2. `docker restart robot_brain`.
3. Confirm with `netstat -ano -p UDP | Select-String ":5006|:8888"` — both
   present.
4. Confirm real packets land with a raw listener inside the container before
   relaunching the ROS2 stack (the ports are free while the stack is down).
5. `ros2 launch launch_all.py`.

---

# REVISION 2 — idle-reaping TESTED and REFUTED (2026-08-05, same session)

**Both theories in this document are now unsupported. The cause of a mapping
disappearing at runtime is UNDETERMINED. Do not repeat either theory as fact.**

## The controlled test

Two throwaway containers, identical except for traffic, both mappings confirmed
created at the start:

| Container | Host port | Traffic |
|---|---|---|
| `idletest` | 15006 | **none, ever** |
| `busytest` | 15007 | one packet every 20s |

Polled every 60s for 9.3 minutes, then re-checked at 10 minutes:

```
13:53:06  idle15006=ALIVE  busy15007=ALIVE
...
14:01:06  idle15006=ALIVE  busy15007=ALIVE
(re-checked at ~10 min: both still ALIVE)
```

**The idle mapping never died.** It survived past the 10.4-minute window inside
which the real 5006 disappeared. If idleness alone caused reaping, this test
would have caught it.

## What this means

- **Idle-reaping (Revision 1): refuted** at this timescale. The correlation that
  suggested it — traffic-carrying ports surviving while 5006 died — was real but
  is not explained by idleness itself.
- **Startup timing (original theory): still untested,** and it never explained
  why an already-working mapping would vanish 10 minutes after a restart with
  Docker fully initialized. Testing it requires restarting Docker Desktop.

## What remains SOLIDLY established (independent of mechanism)

These are direct observations, not theory:

1. A mapping verified as bound on the host **can disappear at runtime**, with no
   restart and no config change. Observed on 5006 between 20:16 and 20:26 UTC.
2. `docker ps` / `docker port` continue to report the mapping as published after
   it is gone. Docker's bookkeeping and the host socket state disagree.
3. The daemon refuses any other container's claim on the port while the original
   container holds it (`port is already allocated`).
4. `docker restart <container>` reliably recreates it. Confirmed twice.
5. Restarting **while the sensor is actually transmitting**, then verifying with
   a raw listener before relaunching the ROS2 stack, produced a working result
   end-to-end (819 LiDAR + 710 IMU packets in 10s).

**The recovery procedure works and is unaffected by any of this.** Only the
explanation is open.

## Next hypothesis under test

**Traffic arriving with no listener inside the container.** When the host proxy
forwards a UDP packet into the container and nothing is bound there, the
container's kernel replies ICMP port-unreachable. A Windows UDP socket that
receives ICMP port-unreachable can be failed with `WSAECONNRESET`, and a proxy
that doesn't handle that could tear the socket down.

This fits the 5006 timeline better than idleness: after the restart there was a
~4-minute gap where the ROS2 stack was down, so nothing inside the container was
bound to 5006 while packets may have been arriving.

**It does not cleanly explain why 8888 survived the same gap** — unless the
LiDAR genuinely was not transmitting at that time, which is the still-open
question recorded in `investigation-log.md`. Result of this test to be appended
below when it completes.
