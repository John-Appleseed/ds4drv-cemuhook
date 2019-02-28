from threading import Thread
import socket
import struct
from binascii import crc32
from time import time


class Message(list):
    Types = dict(version=bytes([0x00, 0x00, 0x10, 0x00]),
                 ports=bytes([0x01, 0x00, 0x10, 0x00]),
                 data=bytes([0x02, 0x00, 0x10, 0x00]))

    def __init__(self, message_type, data):
        self.extend([
            0x44, 0x53, 0x55, 0x53,  # DSUS,
            0xE9, 0x03,  # protocol version (1001),
        ])

        # data length
        self.extend((len(data) + 4).to_bytes(2, byteorder='little'))

        self.extend([
            0x00, 0x00, 0x00, 0x00,  # place for CRC32
            0xff, 0xff, 0xff, 0xff,  # server ID
        ])

        self.extend(Message.Types[message_type])  # data type

        self.extend(data)

        # CRC32
        self[8:12] = crc32(bytes(self)).to_bytes(4, byteorder='little')


class UDPServer:
    def __init__(self, host='', port=26760):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.counter = 0
        self.client = None
        self.remap = False

    def _res_ports(self, index):
        return Message('ports', [
            index,  # pad id
            0x02,  # state (connected)
            0x03,  # model (generic)
            0x01,  # connection type (usb)
            0x00, 0x00, 0x00, 0x00, 0x00, 0xff,  # MAC 00:00:00:00:00:FF
            0xef,  # battery (charged)
            0x00,  # ?
        ])

    def _req_ports(self, message, address):
        requests_count = int.from_bytes(message[20:24], byteorder='little')
        for i in range(requests_count):
            index = message[24 + i]
            if (index != 0):  # we have only one controller
                continue
            self.sock.sendto(bytes(self._res_ports(index)), address)

    def _req_data(self, message, address):
        flags = message[24]
        reg_id = message[25]
        # reg_mac = message[26:32]

        if flags == 0 and reg_id == 0:  # TODO: Check MAC
            self.client = address
            self.last_request = time()

    def _handle_request(self, request):
        message, address = request

        # client_id = message[12:16]
        msg_type = message[16:20]

        if msg_type == Message.Types['version']:
            return
        elif msg_type == Message.Types['ports']:
            self._req_ports(message, address)
        elif msg_type == Message.Types['data']:
            self._req_data(message, address)
        else:
            print('Unknown message type: ' + str(msg_type))

    def report(self, report):
        if not self.client:
            return None

        data = [
            0x00,  # pad id
            0x02,  # state (connected)
            0x02,  # model (generic)
            0x01,  # connection type (usb)
            0x00, 0x00, 0x00, 0x00, 0x00, 0xff,  # MAC 00:00:00:00:00:FF
            0xef,  # battery (charged)
            0x01  # is active (true)
        ]

        data.extend(struct.pack('<I', self.counter))
        self.counter += 1

        buttons1 = 0x00
        buttons1 |= report.button_share
        buttons1 |= report.button_l3 << 1
        buttons1 |= report.button_r3 << 2
        buttons1 |= report.button_options << 3
        buttons1 |= report.dpad_up << 4
        buttons1 |= report.dpad_right << 5
        buttons1 |= report.dpad_down << 6
        buttons1 |= report.dpad_left << 7

        buttons2 = 0x00
        buttons2 |= report.button_l2
        buttons2 |= report.button_r2 << 1
        buttons2 |= report.button_l1 << 2
        buttons2 |= report.button_r1 << 3
        if not self.remap:
            buttons2 |= report.button_triangle << 4
            buttons2 |= report.button_circle << 5
            buttons2 |= report.button_cross << 6
            buttons2 |= report.button_square << 7
        else:
            buttons2 |= report.button_triangle << 4
            buttons2 |= report.button_circle << 7
            buttons2 |= report.button_cross << 5
            buttons2 |= report.button_square << 6

        data.extend([
            buttons1, buttons2,
            0xFF if report.button_ps else 0x00,
            0xFF if report.button_trackpad else 0x00,

            report.left_analog_x,
            255 - report.left_analog_y,
            report.right_analog_x,
            255 - report.right_analog_y,

            0xFF if report.dpad_left else 0x00,
            0xFF if report.dpad_down else 0x00,
            0xFF if report.dpad_right else 0x00,
            0xFF if report.dpad_up else 0x00,

            0xFF if report.button_square else 0x00,
            0xFF if report.button_cross else 0x00,
            0xFF if report.button_circle else 0x00,
            0xFF if report.button_triangle else 0x00,

            0xFF if report.button_r1 else 0x00,
            0xFF if report.button_l1 else 0x00,

            report.r2_analog,
            report.l2_analog,

            0xFF if report.trackpad_touch0_active else 0xFF,
            report.trackpad_touch0_id,

            report.trackpad_touch0_x >> 8,
            report.trackpad_touch0_x & 255,
            report.trackpad_touch0_y >> 8,
            report.trackpad_touch0_y & 255,

            0xFF if report.trackpad_touch1_active else 0xFF,
            report.trackpad_touch1_id,

            report.trackpad_touch1_x >> 8,
            report.trackpad_touch1_x & 255,
            report.trackpad_touch1_y >> 8,
            report.trackpad_touch1_y & 255,
        ])

        data.extend(struct.pack('<d', time() * 10**6))

        sensors = [
            report.orientation_roll / 8192,
            - report.orientation_yaw / 8192,
            - report.orientation_pitch / 8192,
            report.motion_y / 64,
            - report.motion_x / 64 * 4,
            - report.motion_z / 64,
        ]

        for sensor in sensors:
            data.extend(struct.pack('<f', float(sensor)))

        self.sock.sendto(bytes(Message('data', data)), self.client)

    def _worker(self):
        while True:
            self._handle_request(self.sock.recvfrom(1024))

    def start(self):
        self.thread = Thread(target=self._worker)
        self.thread.daemon = True
        self.thread.start()
