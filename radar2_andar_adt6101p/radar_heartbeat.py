"""Read heart rate / breathing from the Andar ADT6101P 60 GHz radar (ADTM6101P module
on the ADTE2104 carrier board) over its USB-C serial port.

The module is assumed to speak TinyFrame, like other ADT6101P vital-sign modules
(HLK-LD6002 / Seeed MR60BHA2):
    SOF(0x01) ID(2) LEN(2) TYPE(2) HEAD_CKSUM(1) DATA(LEN) DATA_CKSUM(1, only if LEN>0)
    header fields big-endian, DATA little-endian, checksum = ~(XOR of bytes) & 0xFF
Known vital-sign TYPEs (from the LD6002/MR60BHA2 protocol, unverified on this board):
    0x0A13 phases (total, breath, heart: 3 x float32)
    0x0A14 breathing rate (float32, breaths/min)
    0x0A15 heart rate     (float32, beats/min)
    0x0A16 distance       (uint32 flag + float32 metres)
Any other TYPE is printed as hex so we can learn what this firmware sends.

Usage:
    python radar_heartbeat.py                 # auto-detect port and baud, run until Ctrl+C
    python radar_heartbeat.py --port COM5 --baud 115200 --seconds 60
    python radar_heartbeat.py --raw           # just hex-dump bytes (debugging)
"""
import argparse
import csv
import struct
import sys
import time
from collections import Counter
from datetime import datetime

import serial
from serial.tools import list_ports

BAUDS = [115200, 1382400, 921600, 460800, 256000, 230400]
TYPE_NAMES = {0x0A13: "phase", 0x0A14: "breath_rate", 0x0A15: "heart_rate", 0x0A16: "distance"}


def cksum(data):
    x = 0
    for b in data:
        x ^= b
    return (~x) & 0xFF


class TinyFrameParser:
    """Feed raw bytes in, get complete (frame_id, type, data) tuples out."""

    def __init__(self):
        self.buf = bytearray()
        self.bad = 0

    def feed(self, chunk):
        self.buf += chunk
        frames = []
        while True:
            start = self.buf.find(b"\x01")
            if start < 0:
                self.buf.clear()
                break
            del self.buf[:start]
            if len(self.buf) < 8:
                break
            head = self.buf[:7]
            if cksum(head) != self.buf[7]:
                self.bad += 1
                del self.buf[0]
                continue
            fid, length, ftype = struct.unpack(">HHH", head[1:7])
            if length > 1024:
                self.bad += 1
                del self.buf[0]
                continue
            total = 8 + length + (1 if length else 0)
            if len(self.buf) < total:
                break
            data = bytes(self.buf[8:8 + length])
            if length and cksum(data) != self.buf[8 + length]:
                self.bad += 1
                del self.buf[0]
                continue
            frames.append((fid, ftype, data))
            del self.buf[:total]
        return frames


def decode(ftype, data):
    """Return a dict of named values, or None if the TYPE is unknown/short."""
    try:
        if ftype == 0x0A13 and len(data) >= 12:
            total, breath, heart = struct.unpack("<fff", data[:12])
            return {"total_phase": total, "breath_phase": breath, "heart_phase": heart}
        if ftype == 0x0A14 and len(data) >= 4:
            return {"breath_rate": struct.unpack("<f", data[:4])[0]}
        if ftype == 0x0A15 and len(data) >= 4:
            return {"heart_rate": struct.unpack("<f", data[:4])[0]}
        if ftype == 0x0A16 and len(data) >= 8:
            flag, dist = struct.unpack("<If", data[:8])
            return {"distance_valid": flag, "distance_m": dist}
    except struct.error:
        pass
    return None


def pick_port(requested):
    if requested:
        return requested
    ports = list(list_ports.comports())
    if not ports:
        sys.exit("No serial ports found. Is the board plugged in with a DATA cable? "
                 "Check Device Manager > Ports (COM & LPT).")
    for p in ports:
        print(f"  found {p.device}: {p.description} [{p.hwid}]")
    usb = [p for p in ports if "USB" in (p.hwid or "")]
    chosen = (usb or ports)[0].device
    print(f"Using {chosen} (pass --port to choose another)")
    return chosen


def detect_baud(port, listen=2.5):
    """Try each baud rate and keep the one that yields valid TinyFrame frames."""
    best, best_frames = None, 0
    for baud in BAUDS:
        try:
            with serial.Serial(port, baud, timeout=0.2) as s:
                s.reset_input_buffer()
                parser, n, nbytes = TinyFrameParser(), 0, 0
                end = time.time() + listen
                while time.time() < end:
                    chunk = s.read(4096)
                    nbytes += len(chunk)
                    n += len(parser.feed(chunk))
        except serial.SerialException as e:
            sys.exit(f"Cannot open {port}: {e}")
        print(f"  {baud:>8} baud: {nbytes:6} bytes, {n:4} valid frames")
        if n > best_frames:
            best, best_frames = baud, n
    return best


def raw_dump(port, baud, seconds):
    with serial.Serial(port, baud, timeout=0.2) as s:
        end = time.time() + seconds
        while time.time() < end:
            chunk = s.read(64)
            if chunk:
                print(chunk.hex(" "))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="e.g. COM5 (auto-detected if omitted)")
    ap.add_argument("--baud", type=int, help="auto-detected if omitted")
    ap.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = until Ctrl+C)")
    ap.add_argument("--raw", action="store_true", help="hex-dump raw bytes instead of parsing")
    ap.add_argument("--csv", default=f"radar_log_{datetime.now():%Y%m%d_%H%M%S}.csv")
    args = ap.parse_args()

    port = pick_port(args.port)
    baud = args.baud
    if args.raw:
        raw_dump(port, baud or 115200, args.seconds or 10)
        return
    if not baud:
        print("Detecting baud rate...")
        baud = detect_baud(port)
        if not baud:
            sys.exit("No valid frames at any baud rate. Run with --raw --baud 115200 and share the output.")
        print(f"Using {baud} baud")

    seen = Counter()
    parser = TinyFrameParser()
    hr_values = []
    with serial.Serial(port, baud, timeout=0.2) as s, open(args.csv, "w", newline="") as f:
        log = csv.writer(f)
        log.writerow(["time", "type_hex", "name", "values", "raw_hex"])
        print(f"Logging to {args.csv}. Sit 0.5-1 m in front of the radar, chest facing it, stay still. Ctrl+C to stop.\n")
        end = time.time() + args.seconds if args.seconds else None
        try:
            while end is None or time.time() < end:
                for fid, ftype, data in parser.feed(s.read(4096)):
                    seen[ftype] += 1
                    values = decode(ftype, data)
                    name = TYPE_NAMES.get(ftype, "unknown")
                    log.writerow([f"{time.time():.3f}", f"0x{ftype:04X}", name, values or "", data.hex()])
                    if ftype == 0x0A15 and values:
                        hr = values["heart_rate"]
                        hr_values.append(hr)
                        print(f"{datetime.now():%H:%M:%S}  HEART RATE  {hr:6.1f} bpm")
                    elif ftype == 0x0A14 and values:
                        print(f"{datetime.now():%H:%M:%S}  breathing   {values['breath_rate']:6.1f} /min")
                    elif ftype == 0x0A16 and values:
                        if values["distance_valid"]:
                            print(f"{datetime.now():%H:%M:%S}  distance    {values['distance_m']:6.2f} m")
                    elif ftype not in TYPE_NAMES and seen[ftype] <= 3:
                        print(f"  new frame type 0x{ftype:04X}, {len(data)} bytes: {data.hex(' ')}")
        except KeyboardInterrupt:
            pass

    print("\nFrame types received:", {f"0x{t:04X}": n for t, n in seen.most_common()})
    print(f"Checksum/sync errors: {parser.bad}")
    valid = [v for v in hr_values if v > 0]
    if valid:
        print(f"Heart rate: {len(valid)} readings, median {sorted(valid)[len(valid)//2]:.1f} bpm")
    else:
        print("No heart-rate frames (0x0A15) decoded. Share the 'Frame types received' line above.")


if __name__ == "__main__":
    main()
