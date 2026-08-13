<#
Compile and flash the ESP32 firmware.

    .\deploy-esp32.ps1              # over WiFi (OTA) - no cable needed
    .\deploy-esp32.ps1 -Usb         # over USB, for bootstrapping or recovery
    .\deploy-esp32.ps1 -Ip 1.2.3.4  # OTA to an explicit address
    .\deploy-esp32.ps1 -CompileOnly # syntax check, touch nothing

OTA requires firmware that already contains ArduinoOTA. If the board is bricked
or running pre-OTA firmware, use -Usb once to recover.
#>
param(
    [switch]$Usb,
    [switch]$CompileOnly,
    [string]$Ip
)

. "$PSScriptRoot\robot-env.ps1"

if (-not (Test-Path $ArduinoCli)) { Write-Error "arduino-cli not found at $ArduinoCli"; exit 1 }

Write-Host "==> compiling $Sketch" -ForegroundColor Cyan
# NOTE: do NOT use --output-dir. The esp32 3.3.5 platform has a post-build hook
# that copies partitions.csv out of <sketch>\build\<fqbn>\, and --output-dir
# moves the artifacts so the hook fails with exit 1 even though the compile
# itself succeeded. --export-binaries writes exactly where the hook expects.
& $ArduinoCli compile --fqbn $Fqbn --export-binaries $Sketch
if ($LASTEXITCODE -ne 0) { Write-Error "compile FAILED - nothing was flashed"; exit 1 }

$exportDir = Join-Path $Sketch ("build\" + ($Fqbn -replace ':', '.'))
$bin = Join-Path $exportDir "sketch_dec30a.ino.bin"
if (-not (Test-Path $bin)) {
    $found = Get-ChildItem $exportDir -Filter "*.ino.bin" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $found) { Write-Error "compiled OK but no .bin found under $exportDir"; exit 1 }
    $bin = $found.FullName
}
Write-Host "    binary: $bin ($([math]::Round((Get-Item $bin).Length/1KB)) KB)" -ForegroundColor DarkGray

if ($CompileOnly) { Write-Host "compile-only, done." -ForegroundColor Green; exit 0 }

if ($Usb) {
    $port = Get-Esp32SerialPort
    if (-not $port) { Write-Error "no COM port found - is the ESP32 plugged in?"; exit 1 }
    Write-Host "==> flashing over USB on $port" -ForegroundColor Cyan
    & $ArduinoCli upload -p $port --fqbn $Fqbn $Sketch
    if ($LASTEXITCODE -ne 0) { Write-Error "USB upload FAILED"; exit 1 }
    Write-Host "USB flash OK" -ForegroundColor Green
    exit 0
}

if (-not $Ip) { $Ip = Get-Esp32Ip }
if (-not $Ip) {
    Write-Error "could not find the ESP32 on the network. Is it powered on and on WiFi? Otherwise use -Usb."
    exit 1
}

Write-Host "==> OTA flashing to $Ip`:$OtaPort" -ForegroundColor Cyan
Write-Host "    (motors are stopped by the firmware before the update begins)" -ForegroundColor DarkGray
$callbackPort = 3233
Get-Process espota -ErrorAction SilentlyContinue | Stop-Process -Force
& $Espota -i $Ip -p $OtaPort -P $callbackPort -f $bin -r
if ($LASTEXITCODE -ne 0) {
    Write-Error "OTA FAILED. The board keeps running its previous firmware. If it is unreachable, recover with -Usb."
    exit 1
}
Write-Host "OTA flash OK - board is rebooting" -ForegroundColor Green
