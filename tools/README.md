# Robot operations tools

Windows PowerShell entry points:

- `robot-up.ps1 -Full` recreates Docker port mappings, starts one ROS and
  RobotServer stack, and verifies sensor health.
- `deploy-esp32.ps1` compiles and deploys ESP32 firmware over OTA or USB.
- `esp32-logs.ps1` receives remote ESP32 diagnostics on UDP 8890.
- `deploy-pi.ps1` deploys files to the Raspberry Pi.
- `find-devices.ps1` reports reachable robot components.

The scripts assume the ESP32 repository is checked out at
`~/Documents/Arduino/RobotController/sketch_dec30a` and that the Docker
containers are named `robot_brain` and `my-robot-server`.

Pi service files are under `pi/`. Install `robot-webcam.service` as a user
service to start and restart the webcam streamer independently of VNC or SSH.
