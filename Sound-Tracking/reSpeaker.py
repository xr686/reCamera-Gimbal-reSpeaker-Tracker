import sys
import struct
import usb.core
import usb.util
import time
import socket
import json

# name, resid, cmdid, length, type
PARAMETERS = {
    "VERSION":          (48,  0,  3,  "ro", "uint8"),
    "AEC_AZIMUTH_VALUES": (33, 75, 16, "ro", "radians"),
    "DOA_VALUE":        (20, 18,  4,  "ro", "uint16"),   # 4 bytes → two uint16 words
    "REBOOT":           (48,  7,  1,  "wo", "uint8"),
}

class ReSpeaker:
    TIMEOUT = 100000  # USB timeout

    def __init__(self, dev):
        self.dev = dev

    def write(self, name, data_list):
        try:
            data = PARAMETERS[name]
        except KeyError:
            return

        if data[3] == "ro":
            raise ValueError('{} is read-only'.format(name))

        if len(data_list) != data[2]:
            raise ValueError('{} value count is not {}'.format(name, data[2]))

        windex   = data[0]
        wvalue   = data[1]
        data_type = data[4]
        data_cnt  = data[2]
        payload   = []

        if data_type in ('float', 'radians'):
            for i in range(data_cnt):
                payload += struct.pack(b'f', float(data_list[i]))
        elif data_type in ('char', 'uint8'):
            for i in range(data_cnt):
                payload += data_list[i].to_bytes(1, byteorder='little')
        else:
            for i in range(data_cnt):
                payload += struct.pack(b'i', data_list[i])

        print("WriteCMD: cmdid: {}, resid: {}, payload: {}".format(wvalue, windex, payload))
        self.dev.ctrl_transfer(
            usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
            0, wvalue, windex, payload, self.TIMEOUT)

    def read(self, name):
        try:
            data = PARAMETERS[name]
        except KeyError:
            return

        resid  = data[0]
        cmdid  = 0x80 | data[1]
        length = data[2] + 1        # +1 for the leading status byte

        response = self.dev.ctrl_transfer(
            usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
            0, cmdid, resid, length, self.TIMEOUT)

        byte_data = response.tobytes()

        if data[4] == 'uint8':
            result = response.tolist()

        elif data[4] == 'radians':
            num_floats = (length - 1) // 4           # each float = 4 bytes
            fmt = '<' + 'f' * num_floats
            result = list(struct.unpack(fmt, byte_data[1:1 + num_floats * 4]))

        elif data[4] == 'uint16':
            # ── FIX ──────────────────────────────────────────────────────────
            # byte_data[0]      = status byte (skip it)
            # byte_data[1:...]  = payload: N little-endian uint16 words
            # Each word is 2 bytes, so num_words = data[2] / 2
            num_words = data[2] // 2                 # 4 bytes → 2 words
            fmt = '<' + 'H' * num_words              # unsigned 16-bit, little-endian
            result = list(struct.unpack(fmt, byte_data[1:1 + num_words * 2]))
            # ─────────────────────────────────────────────────────────────────

        return result

    def close(self):
        usb.util.dispose_resources(self.dev)

def find(vid=0x2886, pid=0x001A):
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if not dev:
        return
    return ReSpeaker(dev)


def main():
    dev = find()
    if not dev:
        print('No device found')
        sys.exit(1)
    print('{}: {}'.format("VERSION", dev.read("VERSION")))

    # === 新增：配置UDP socket ===
    UDP_IP = "192.168.31.198"  # reCamera 的 IP 地址
    UDP_PORT = 18888  # 自定义的通信端口
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"开始将声源数据通过 UDP 发送至 {UDP_IP}:{UDP_PORT}")
    # ============================

    while True:
        result = dev.read("DOA_VALUE")
        doa_angle = result[0]  # 0–359 degrees
        speech_active = result[1]  # VAD flag: 1 = speech, 0 = silence

        print('SPEECH_DETECTED: {}, DOA_VALUE: {}'.format(speech_active, doa_angle))

        # === 新增：打包并发送数据 ===
        payload = {
            "doa": doa_angle,
            "vad": speech_active
        }
        sock.sendto(json.dumps(payload).encode('utf-8'), (UDP_IP, UDP_PORT))
        # ============================

        time.sleep(0.1)

    dev.close()


if __name__ == '__main__':
    main()