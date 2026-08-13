param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Name
)

$ErrorActionPreference = "Stop"

if ($Name -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]*$') {
    throw "Map names may contain only letters, numbers, underscores, and hyphens"
}

docker exec robot_brain bash -lc `
    "source /opt/ros/humble/setup.bash && python3 /root/code/save_map.py '$Name'"

if ($LASTEXITCODE -ne 0) {
    throw "Map save failed"
}
