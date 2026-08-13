"""Print the newest usable VS Code X11 display socket number."""

import os
import socket


socket_dir = "/tmp/.X11-unix"
candidates = sorted(
    (name for name in os.listdir(socket_dir)
     if name.startswith("X") and name[1:].isdigit()),
    key=lambda name: os.path.getmtime(os.path.join(socket_dir, name)),
    reverse=True,
)

for name in candidates[:10]:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(1)
    try:
        connection.connect(os.path.join(socket_dir, name))
        connection.sendall(
            b'l\x00\x0b\x00\x00\x00\x00\x00\x00\x00\x00\x00')
        reply = connection.recv(8)
        if reply and reply[0] == 1:
            print(name[1:])
            break
    except Exception:
        pass
    finally:
        connection.close()
