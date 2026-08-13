import socket


class LidarUdpReceiver:
    def __init__(self, port=5006):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Keep the port held while bridge processes overlap during restarts.
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Absorb short scheduling pauses without kernel-level packet loss.
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        self.sock.bind(("0.0.0.0", port))
        self.sock.setblocking(False)

    def get_available_packets(self):
        packets = []
        try:
            while True:
                data, _ = self.sock.recvfrom(1024)
                packets.append(data)
        except BlockingIOError:
            pass

        return packets
