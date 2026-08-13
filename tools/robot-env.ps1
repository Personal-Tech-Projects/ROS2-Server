# Shared environment + device discovery for the robot toolchain.
# Dot-source this from the other scripts:  . "$PSScriptRoot\robot-env.ps1"

$ArduinoCli = "C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"
$Espota     = "$env:LOCALAPPDATA\Arduino15\packages\esp32\hardware\esp32\3.3.5\tools\espota.exe"
$Fqbn       = "esp32:esp32:esp32doit-devkit-v1"
$Sketch     = Join-Path $env:USERPROFILE "Documents\Arduino\RobotController\sketch_dec30a"
$BuildDir   = Join-Path $PSScriptRoot "build"
$PiHost     = "robopi"          # ssh alias in ~/.ssh/config
$OtaPort    = 3232
$LogPort    = 8890

# The ESP32 uses DHCP and its address has drifted repeatedly (.78 -> .111 ->
# .114). Rather than pinning a static IP (which risks colliding with the DHCP
# pool), discover it: the firmware opens a TCP connection to port 5005 on this
# PC every second, so its current address is always in the local TCP table.
function Get-Esp32Ip {
    # Lines look like:
    #   TCP    192.168.4.81:5005      192.168.4.114:50215    TIME_WAIT       0
    # The REMOTE address is the ESP32. Anchor on the whole line so we cannot
    # accidentally capture a port number or the local address.
    $counts = @{}
    foreach ($line in (netstat -ano -p TCP)) {
        if ($line -match '^\s*TCP\s+\S+:5005\s+(\d{1,3}(?:\.\d{1,3}){3}):\d+\s+\S+') {
            $ip = $Matches[1]
            if ($ip -ne '0.0.0.0' -and $ip -ne '127.0.0.1') {
                $counts[$ip] = 1 + $(if ($counts.ContainsKey($ip)) { $counts[$ip] } else { 0 })
            }
        }
    }
    if ($counts.Count -eq 0) { return $null }
    # most frequently seen remote peer = the thing pinging us once a second
    return ($counts.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 1).Key
}

# An open port 22 is NOT proof it is the Pi - this used to report 192.168.4.28,
# an unrelated machine. Authenticate with the robot key and check the hostname.
function Test-IsPi($ip) {
    $name = & ssh -i "$env:USERPROFILE\.ssh\id_ed25519_robot" -o BatchMode=yes `
        -o StrictHostKeyChecking=no -o ConnectTimeout=5 "jjlav@$ip" hostname 2>$null
    return ($LASTEXITCODE -eq 0 -and $name -match "raspberrypup")
}

function Get-PiIp {
    $known = "192.168.7.20"
    if ((Test-TcpPort $known 22 1500) -and (Test-IsPi $known)) { return $known }

    # scan the /22 for port 22, then verify identity on each candidate
    $targets = @()
    4..7 | ForEach-Object { $o = $_; 1..254 | ForEach-Object { $targets += "192.168.$o.$_" } }
    $tasks = @{}
    foreach ($t in $targets) {
        $c = New-Object System.Net.Sockets.TcpClient
        $tasks[$t] = @{ c = $c; r = $c.BeginConnect($t, 22, $null, $null) }
    }
    Start-Sleep -Milliseconds 1800
    $candidates = @()
    foreach ($t in $targets) {
        $x = $tasks[$t]
        if ($x.r.AsyncWaitHandle.WaitOne(0) -and $x.c.Connected) { $candidates += $t }
        try { $x.c.Close() } catch {}
    }
    foreach ($ip in $candidates) { if (Test-IsPi $ip) { return $ip } }
    return $null
}

function Test-TcpPort($ip, $port, $timeoutMs) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $r = $c.BeginConnect($ip, $port, $null, $null)
        $ok = $r.AsyncWaitHandle.WaitOne($timeoutMs) -and $c.Connected
    } catch { $ok = $false } finally { $c.Close() }
    return $ok
}

function Get-Esp32SerialPort {
    $p = [System.IO.Ports.SerialPort]::GetPortNames()
    if ($p) { return $p[0] } else { return $null }
}
