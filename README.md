# ROS2 Server

## Save a map

The `robot_brain` container mounts the Windows map folder at
`/root/robot-maps`. Save the current SLAM Toolbox map from PowerShell with:

```powershell
powershell.exe -ExecutionPolicy Bypass -File C:\Users\jjlav\robot-tools\save-map.ps1 home_v1
```

The command creates four files:

- `.posegraph` and `.data` for SLAM Toolbox localization or continued mapping.
- `.yaml` and `.pgm` for occupancy-map tools such as Nav2.

Use a new versioned name for every validated map. Existing map files are not
overwritten.
