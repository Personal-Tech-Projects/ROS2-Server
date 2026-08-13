"""Temporarily bind and drain UDP ports during service restarts.

This prevents ICMP port-unreachable responses from breaking Docker Desktop's
host forwarding. The timeout prevents the keeper from lingering indefinitely.
"""
import argparse
import socket
import time

p = argparse.ArgumentParser()
p.add_argument('--seconds', type=float, default=120.0)
p.add_argument('--ports', type=str, default='5006,8888',
               help='comma-separated UDP ports to hold')
args = p.parse_args()

ports = [int(x) for x in args.ports.split(',') if x.strip()]

socks = []
for port in ports:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", port))
    except OSError as e:
        print("port_keeper: cannot bind %d: %s" % (port, e), flush=True)
        continue
    s.setblocking(False)
    socks.append(s)

print("port_keeper: holding %s for %.0fs" %
      (",".join(str(x) for x in ports), args.seconds), flush=True)

end = time.time() + args.seconds
n = 0
while time.time() < end:
    for s in socks:
        try:
            while True:
                s.recvfrom(65535)
                n += 1
        except BlockingIOError:
            pass
        except OSError:
            pass
    time.sleep(0.005)

for s in socks:
    s.close()
print("port_keeper: released (drained %d datagrams)" % n, flush=True)
