# Windows host / Docker Desktop networking reference

Written from a Claude Code session running directly on the Windows host
(`C:\Users\jjlav`), which runs Docker Desktop and the `robot_brain` container.
This file is host-side context — it complements `system-overview.md`'s
"Known flaky behavior" section.

## Container basics (from `docker ps` / `docker inspect`)

- Container name: `robot_brain`, image `ros2-slam-bot:latest`, id starts `32675c04d75e`.
- `NetworkMode: bridge` (Docker's default bridge, not `host`).
- Published ports: `5006/udp` and `8888/udp` only. **TCP 5005 (the ESP32
  firmware's handshake gate) is NOT published on this container at all** —
  confirms that handshake is answered by a separate "robot server", not this
  container. See `system-overview.md`.
- `RestartPolicy: {"Name": "no"}` — container will NOT auto-restart if its
  main process dies. `StopTimeout: 1` — only 1s grace period on stop/restart
  before SIGKILL.
- `"Mounts": []` — **`/root/code` is NOT bind-mounted to any Windows path.**
  It lives purely inside the container's own filesystem/image layer. This
  matters for editing files in `/root/code` (including this `brain/`
  directory) from a Windows-host session — see "Editing container files from
  Windows" below.

## Host networking topology

- Windows Wi-Fi adapter named `Wi-Fi` (not `Wi-Fi 2` / `Wi-Fi 5`, both
  disconnected) holds IPv4 `192.168.4.81` — this is the exact `server_ip` the
  ESP32 firmware targets. Confirmed correct interface.
- `vEthernet (WSL (Hyper-V firewall))` at `172.26.96.1` — WSL2's NAT network.
- `vEthernet (Default Switch)` at `172.23.16.1` — unrelated Hyper-V default switch.
- Container's own IP inside Docker's bridge network: `172.17.0.2` (confirmed
  from inside the container via a UDP-connect trick, matches docker inspect).

## Docker Desktop backend

- `docker info` -> `Kernel Version: 6.6.87.2-microsoft-standard-WSL2`,
  `Operating System: Docker Desktop`. Confirms **WSL2 backend**.
- Windows-side port forwarding for the published UDP ports is handled by
  `com.docker.backend.exe` (confirmed via `Get-Process -Id <pid from netstat>`).
  At time of checking, this process WAS correctly listening on
  `0.0.0.0:8888`, `0.0.0.0:5006` (and `[::]` equivalents).
- **No `%USERPROFILE%\.wslconfig` file exists** -> WSL2 is running in its
  default **NAT networking mode**, not the newer opt-in **mirrored** mode.
  Default NAT mode is the configuration most associated with "UDP port
  forwarding silently breaks, restart fixes it" reports for Docker
  Desktop + WSL2. Mirrored mode removes most of the NAT-related failure modes
  by having the WSL2 VM share the host's network interfaces directly.

## Windows Firewall

- `Get-NetFirewallRule -DisplayName "*Docker*"` shows inbound Allow rules
  named "Docker Desktop Backend", scoped to `Profile: Public`, `Enabled: True`.
- Host's Wi-Fi network profile is currently `Public` (via
  `Get-NetConnectionProfile`). Since the firewall rule is scoped to Public and
  currently enabled, this is NOT an active blocker right now — but it's a
  plausible trigger if Windows ever re-evaluates the network (sleep/wake,
  router reboot, reconnect) and the profile/rule state gets out of sync
  momentarily. Lower-confidence than the NAT-mode finding.

## Proposed fix (needs explicit user sign-off before doing — affects the whole machine, not just this container)

Enable WSL2 mirrored networking mode:

1. Create `%USERPROFILE%\.wslconfig` with:
   ```ini
   [wsl2]
   networkingMode=mirrored
   ```
2. `wsl --shutdown` (from an elevated or normal PowerShell — shuts down all
   WSL2 distros, including Docker Desktop's).
3. Restart Docker Desktop.
4. Re-test: does the ESP32 connection stay reliable across sleep/wake,
   Wi-Fi reconnects, and long idle periods, without needing a container
   restart?

This is a machine-wide WSL2 setting, not scoped to just this container — any
other WSL2 usage on this machine will also switch to mirrored networking.
Mention this to the user explicitly before applying it.

## Useful commands (Windows host side)

```powershell
docker ps                                          # confirm container running
docker exec robot_brain <cmd>                      # run a one-off command inside the container
docker restart robot_brain                         # the fix-it button we're trying to root-cause
docker info                                        # confirm WSL2 backend, etc.
netstat -ano | findstr ":8888 :5006"                # confirm Windows-side port forwarding is alive, and get the owning PID
Get-Process -Id <pid>                              # identify what that PID is
Get-NetConnectionProfile                           # check network profile (Public/Private) per interface
Get-NetFirewallRule -DisplayName "*Docker*"        # check Docker's firewall rules and profile scoping
```

## Editing container files from Windows

`/root/code` is NOT bind-mounted (`"Mounts": []` in docker inspect), so the
Windows-host Read/Write/Edit tools can't touch it directly. Round-trip via
`docker cp` instead:

```powershell
docker cp robot_brain:/root/code/brain/some-file.md "$env:TEMP\some-file.md"
# ... edit the local temp copy with Read/Edit tools ...
docker cp "$env:TEMP\some-file.md" robot_brain:/root/code/brain/some-file.md
```
