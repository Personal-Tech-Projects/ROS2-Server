import struct



def decode_packet(data):

    if len(data) != 47 or data[0] != 0x54 or data[1] != 0x2C:

        return []

   

    start_angle = struct.unpack_from('<H', data, 4)[0] * 0.01

    end_angle = struct.unpack_from('<H', data, 42)[0] * 0.01

   

    angle_step = (end_angle - start_angle) / 11.0

    if angle_step < 0:

        angle_step += (360.0 / 11.0)



    points = []

    for i in range(12):

        idx = 6 + (i * 3)

        distance_m = struct.unpack_from('<H', data, idx)[0] / 1000.0

        point_angle = (start_angle + (i * angle_step)) % 360.0

       

        if distance_m > 0.1:

            points.append((point_angle, distance_m))

           

    return points