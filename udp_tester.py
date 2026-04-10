import socket

# Listen on all available network interfaces
UDP_IP = "0.0.0.0"
UDP_PORT = 5006

# Create a UDP socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

print(f"Listening for LiDAR UDP packets on port {UDP_PORT}...")
print("Press Ctrl+C to stop.")

try:
    while True:
        # Wait for a packet to arrive (buffer size 1024 bytes)
        data, addr = sock.recvfrom(1024)
        print(f"SUCCESS: Received {len(data)} bytes from ESP32 at {addr}")
except KeyboardInterrupt:
    print("\nTest stopped.")