<# Show where every part of the robot currently is, and whether it is healthy. #>
. "$PSScriptRoot\robot-env.ps1"

Write-Host "=== ESP32 ===" -ForegroundColor Cyan
$ip = Get-Esp32Ip
if ($ip) { Write-Host "  network : $ip  (discovered from its TCP pings to :5005)" -ForegroundColor Green }
else     { Write-Host "  network : NOT FOUND - powered off, or not on WiFi" -ForegroundColor Red }
$com = Get-Esp32SerialPort
if ($com) { Write-Host "  usb     : $com" } else { Write-Host "  usb     : not plugged in (fine - OTA does not need it)" -ForegroundColor DarkGray }
if ($ip) {
    Write-Host "  ota     : target discoverable; verify with deploy-esp32.ps1" -ForegroundColor Green
}

Write-Host "`n=== Raspberry Pi ===" -ForegroundColor Cyan
if (Test-TcpPort "192.168.7.20" 22 2500) {
    Write-Host "  ssh     : 192.168.7.20:22 open" -ForegroundColor Green
} else {
    Write-Host "  ssh     : 192.168.7.20 not answering - scanning..." -ForegroundColor Yellow
    $pi = Get-PiIp
    if ($pi) { Write-Host "  ssh     : found at $pi" -ForegroundColor Green }
    else     { Write-Host "  ssh     : NOT FOUND - powered off?" -ForegroundColor Red }
}

Write-Host "`n=== Docker ===" -ForegroundColor Cyan
docker ps --format "  {{.Names}}  {{.Status}}"

Write-Host "`n=== Sensor port mappings (the recurring failure) ===" -ForegroundColor Cyan
foreach ($p in 5005, 5006, 8888) {
    $found = netstat -ano -p UDP | Select-String ":$p\s"
    if ($found) { Write-Host "  udp $p : MAPPED" -ForegroundColor Green }
    else        { Write-Host "  udp $p : GONE - restart robot_brain (see brain/docker-port-forwarding-failure.md)" -ForegroundColor Red }
}
