<#
Copy files to the Raspberry Pi and optionally run something afterwards.

    .\deploy-pi.ps1 -Path ..\pi-video-stream\webcam_streamer.py -Dest pi-video-stream/
    .\deploy-pi.ps1 -Path .\myscript.py -Dest robot_stack/ -Run "python3 robot_stack/myscript.py"
    .\deploy-pi.ps1 -Run "ls ~/robot_stack"        # just run a command

Uses the 'robopi' ssh alias (key ~/.ssh/id_ed25519_robot, no passphrase so it
works unattended). The Pi does not answer ICMP - reachability is tested with
TCP 22, never ping.
#>
param(
    [string]$Path,
    [string]$Dest = "",
    [string]$Run
)

. "$PSScriptRoot\robot-env.ps1"

if (-not (Test-TcpPort "192.168.7.20" 22 2500)) {
    Write-Host "Pi not answering at 192.168.7.20 - scanning..." -ForegroundColor Yellow
    $ip = Get-PiIp
    if (-not $ip) { Write-Error "Pi not reachable. Is it powered on?"; exit 1 }
    Write-Host "found Pi at $ip (update ~/.ssh/config if this is permanent)" -ForegroundColor Yellow
}

if ($Path) {
    if (-not (Test-Path $Path)) { Write-Error "no such file: $Path"; exit 1 }
    Write-Host "==> copying $Path -> $PiHost`:$Dest" -ForegroundColor Cyan
    & scp -o BatchMode=yes $Path "${PiHost}:$Dest"
    if ($LASTEXITCODE -ne 0) { Write-Error "scp FAILED"; exit 1 }
    Write-Host "copied" -ForegroundColor Green
}

if ($Run) {
    Write-Host "==> $Run" -ForegroundColor Cyan
    & ssh -o BatchMode=yes $PiHost $Run
}
