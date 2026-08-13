<#
Remote serial monitor for the ESP32, over WiFi.

    .\esp32-logs.ps1              # follow until Ctrl+C
    .\esp32-logs.ps1 -Seconds 30  # capture a fixed window then stop

The firmware mirrors its log lines to UDP $LogPort. Unlike opening COM3, this
does NOT reset the board - which matters, because a reset restarts every
counter and has invalidated several measurements.
#>
param([int]$Seconds = 0)

. "$PSScriptRoot\robot-env.ps1"

$udp = New-Object System.Net.Sockets.UdpClient($LogPort)
$udp.Client.ReceiveTimeout = 1000
$ep = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)

Write-Host "listening for ESP32 logs on UDP $LogPort ... (Ctrl+C to stop)" -ForegroundColor Cyan
$deadline = if ($Seconds -gt 0) { (Get-Date).AddSeconds($Seconds) } else { [DateTime]::MaxValue }
$count = 0
try {
    while ((Get-Date) -lt $deadline) {
        try {
            $bytes = $udp.Receive([ref]$ep)
            $text = [System.Text.Encoding]::ASCII.GetString($bytes)
            Write-Host ("{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $text)
            $count++
        } catch [System.Net.Sockets.SocketException] { }
    }
} finally {
    $udp.Close()
    Write-Host "`n$count lines received" -ForegroundColor DarkGray
    if ($count -eq 0) {
        Write-Host "Nothing arrived. Check: is the ESP32 powered and on WiFi, and is it running firmware with logLine() (UDP $LogPort)?" -ForegroundColor Yellow
    }
}
