import socket



class LidarUdpReceiver:

    def __init__(self, port=5006):

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

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