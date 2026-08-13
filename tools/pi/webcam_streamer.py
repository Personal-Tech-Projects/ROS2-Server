import cv2
import socket
import struct
import math
import time

# --- NETWORK SETTINGS ---
SERVER_IP = "192.168.4.81"
SERVER_PORT = 5005

# Keep the payload below the network MTU after adding the 10-byte header.
MAX_PAYLOAD_SIZE = 1400

def start_stream():
    # 1. Initialize UDP Socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # 2. Initialize Webcam (0 is usually the default USB or built-in camera)
    cap = cv2.VideoCapture(0)

    # Force a lower resolution to keep network traffic smooth (640x480 is perfect for testing)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    print(f"Starting video stream to {SERVER_IP}:{SERVER_PORT}...")
    print("Press Ctrl+C in Thonny to stop.")

    frame_id = 0

    try:
        while True:
            # 3. Read a frame from the webcam
            ret, frame = cap.read()
            if not ret:
                print("Failed to grab frame from camera!")
                time.sleep(1)
                continue

            # 4. Compress the frame to JPEG format in RAM
            # 70 is the quality (0-100). Lower quality = smaller file = faster stream!
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 70]
            success, buffer = cv2.imencode('.jpg', frame, encode_param)

            if not success:
                continue

            # Convert the image buffer to a raw byte array
            jpeg_bytes = buffer.tobytes()
            total_size = len(jpeg_bytes)

            # 5. Calculate how many chunks we need
            total_chunks = math.ceil(total_size / MAX_PAYLOAD_SIZE)

            # 6. Slice the JPEG and send each chunk
            for chunk_index in range(total_chunks):
                # Figure out where this chunk starts and ends
                start_byte = chunk_index * MAX_PAYLOAD_SIZE
                end_byte = min(start_byte + MAX_PAYLOAD_SIZE, total_size)

                payload = jpeg_bytes[start_byte:end_byte]
                chunk_size = len(payload)

                # 7. Create the Binary Header (The "Envelope Label")
                # The '<IHHH' tells Python to format this exactly how C++ expects it:
                # '<' = Little Endian (Standard memory format)
                # 'I' = Unsigned Int 32-bit (frame_id)
                # 'H' = Unsigned Short 16-bit (total_chunks)
                # 'H' = Unsigned Short 16-bit (chunk_index)
                # 'H' = Unsigned Short 16-bit (chunk_size)
                header = struct.pack('<IHHH', frame_id, total_chunks, chunk_index, chunk_size)

                # 8. Combine the header and the image payload, then send it!
                packet = header + payload
                sock.sendto(packet, (SERVER_IP, SERVER_PORT))

            if frame_id % 30 == 0:
                print(f"Sent Frame {frame_id} in {total_chunks} chunks.", flush=True)

            frame_id += 1

            # Optional: A tiny microscopic sleep helps prevent flooding the Pi's network chip
            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nStopping stream...")

    finally:
        # Clean up
        cap.release()
        sock.close()
        print("Camera released and socket closed.")

if __name__ == "__main__":
    start_stream()
