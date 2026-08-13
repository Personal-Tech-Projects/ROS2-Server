# ESP32 firmware: finding, editing, compiling, flashing, and verifying

Confirmed working end-to-end in Session 2 (Windows-host session) on
2026-08-01. This is the whole recipe — follow it exactly, don't re-derive it.

## 1. Find the authoritative ESP32 sketch

The live, compilable project lives on the **Windows host**, not in this
container:
```
C:\Users\jjlav\Documents\Arduino\RobotController\sketch_dec30a\sketch_dec30a.ino
```
The ROS2 repository intentionally does not keep a duplicate firmware copy.
Always edit the real `.ino` file above, on the Windows host (or its dedicated
ESP32 repository).

If this path ever changes, rediscover it with (PowerShell, Windows host):
```powershell
Get-ChildItem -Path "$env:USERPROFILE\Documents\Arduino" -Filter *.ino -Recurse -ErrorAction SilentlyContinue
```
Look for the one matching the known file size / last-write-time of the
current firmware, under a project folder (not under `libraries\...\examples\`).

## 2. Toolchain (already installed, no setup needed)

Arduino IDE 2 is installed and bundles its own `arduino-cli`. Use the full
path directly (no need to add it to `$env:PATH`):
```powershell
$cli = "C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"
```
The `esp32:esp32` core (v3.3.5 at time of writing) is already installed, as
are the required libraries (`Adafruit_BNO08x` etc., under
`Documents\Arduino\libraries\`).

## 3. Board identity

- Board: **DOIT ESP32 DEVKIT V1**
- FQBN: **`esp32:esp32:esp32doit-devkit-v1`**
- `arduino-cli board list` shows it on a COM port but reports the board type
  as "Unknown" (arduino-cli can't always auto-ID generic ESP32 dev boards) —
  that's expected, not an error. Use the FQBN above explicitly.
- If you need to re-find the FQBN for a differently-named board:
  ```powershell
  & $cli board listall esp32 | Select-String -Pattern "<part of the board name>"
  ```

## 4. Find the COM port

```powershell
[System.IO.Ports.SerialPort]::GetPortNames()
```
At time of writing this is `COM3`. Only reliable while the ESP32 is actually
plugged into this Windows machine via USB.

## 5. Compile

```powershell
& $cli compile --fqbn esp32:esp32:esp32doit-devkit-v1 "C:\Users\jjlav\Documents\Arduino\RobotController\sketch_dec30a"
```
Always do this before uploading — catches syntax errors without touching the
hardware. Expect output like:
```
Sketch uses 957399 bytes (73%) of program storage space. Maximum is 1310720 bytes.
Global variables use 50456 bytes (15%) of dynamic memory, leaving 277224 bytes for local variables. Maximum is 327680 bytes.
```
(Exact byte counts are a decent fingerprint — two builds with identical code
produce identical byte counts, useful for confirming a revert actually
restored the exact prior state.)

## 6. Upload / flash

```powershell
& $cli upload -p COM3 --fqbn esp32:esp32:esp32doit-devkit-v1 "C:\Users\jjlav\Documents\Arduino\RobotController\sketch_dec30a"
```
Takes ~15-20s. Ends with `Hard resetting via RTS pin...` — the board reboots
into the new firmware automatically, no manual reset button needed.

## 7. Read serial output (no separate serial-monitor app needed)

PowerShell can talk to the COM port directly:
```powershell
$port = New-Object System.IO.Ports.SerialPort COM3,115200,None,8,one
$port.ReadTimeout = 1000
$port.Open()
$lines = @()
$deadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $deadline) {
  try { $line = $port.ReadLine(); $lines += $line } catch [System.TimeoutException] { }
}
$port.Close()
$lines
```
Baud rate **115200** matches this firmware's `Serial.begin(115200)` — check
the `.ino` if a future firmware version changes that. Opening the port
resets the ESP32 (DTR/RTS toggle on most USB-serial chips), so you'll
typically see the boot sequence from scratch.

## 8. How we PROVED the deploy pipeline actually works (repeat this whenever in doubt)

Don't just trust that "compile succeeded, upload said done" means the new
code is actually running — verify it directly:

1. Add a **unique, unmistakable temporary marker** to the code, in two places:
   - Once in `setup()`, e.g.:
     ```cpp
     Serial.println(">>> DEPLOY VERIFY MARKER: CLAUDE-TEST-8842 (boot) <<<");
     ```
   - A repeating heartbeat at the very top of `loop()`, gated by `millis()`
     so it doesn't spam every iteration:
     ```cpp
     static unsigned long lastDeployHeartbeat = 0;
     if (millis() - lastDeployHeartbeat >= 3000) {
       lastDeployHeartbeat = millis();
       Serial.println(">>> DEPLOY VERIFY HEARTBEAT: CLAUDE-TEST-8842 <<<");
     }
     ```
   Use a fresh unique token each time (timestamp, random suffix, whatever) —
   the point is it can't possibly be leftover output from a previous build.
2. Compile + upload (steps 5-6 above).
3. Read serial (step 7). **Confirm the heartbeat is actually repeating** —
   this is stronger evidence than the one-time boot line, which you can miss
   due to timing between `Open()` (which resets the board) and when your
   read loop actually starts.
4. Once confirmed: remove the temporary marker lines, recompile (byte count
   should match whatever the last known-good build was, if you're reverting
   to it), and reflash.

This whole cycle (edit -> compile -> upload -> verify -> revert -> reflash)
takes about 2 minutes. Worth doing any time you've made a firmware change
and want certainty it's actually live on the hardware, not just "should be."

## Known pitfall

`arduino-cli.exe` is NOT on `$env:PATH` by default — always invoke it via its
full path (step 2), or `Get-Command arduino-cli` will fail with "not
recognized."
