"""Stand-alone parser for TI 3D People Tracking UART frames (IWR6843AOP), so the
service does not need the TI Radar Toolbox installed. Returns the same dict keys as
the Toolbox's parseFrame.parseStandardFrame for the fields service.py uses.

Frame header (40 B, little-endian): magic(8) version totalPacketLen platform frameNumber
    timeCpuCycles numDetectedObj numTLVs subFrameNumber   (8 x uint32)
TLV header (8 B): type(uint32) length(uint32), length = payload bytes (header excluded)
    1020 compressed spherical point cloud: units fffff (elev, azim, doppler, range, snr)
         then per point int8 elev, int8 azim, int16 doppler, uint16 range, uint16 snr
    1010 target list: per target uint32 tid + 27 float32 (pos xyz, vel xyz, acc xyz, ec[16], g, confidence)
    1011 target index: uint8 per point
    1012 target height: per target uint32 tid, float32 maxZ, float32 minZ
    1021 presence indication: uint32
"""
import math
import struct

HEADER = struct.Struct('<8s8I')
TLV_HEADER = struct.Struct('<2I')
POINT_UNIT = struct.Struct('<5f')
POINT = struct.Struct('<2bh2H')
TARGET = struct.Struct('<I27f')
HEIGHT = struct.Struct('<I2f')


def parse_frame(packet, demo=None):
    out = {'error': 0, 'trackData': [], 'heightData': [], 'pointCloud': [], 'numDetectedPoints': 0, 'tlvTypes': []}
    if len(packet) < HEADER.size:
        out['error'] = 1
        return out
    _, version, total, platform, frame_num, _, num_obj, num_tlvs, _ = HEADER.unpack_from(packet, 0)
    out.update(frameNum=frame_num, numDetectedPoints=num_obj)
    offset = HEADER.size
    for _ in range(num_tlvs):
        if offset + TLV_HEADER.size > len(packet):
            out['error'] = 2
            break
        tlv_type, length = TLV_HEADER.unpack_from(packet, offset)
        offset += TLV_HEADER.size
        payload = packet[offset:offset + length]
        offset += length
        if len(payload) < length:
            out['error'] = 3
            break
        out['tlvTypes'].append(tlv_type)
        if tlv_type == 1020 and length >= POINT_UNIT.size:
            elev_u, azim_u, dopp_u, range_u, snr_u = POINT_UNIT.unpack_from(payload, 0)
            points = []
            for i in range((length - POINT_UNIT.size) // POINT.size):
                e, a, d, r, s = POINT.unpack_from(payload, POINT_UNIT.size + i * POINT.size)
                elev, azim, rng = e * elev_u, a * azim_u, r * range_u
                points.append([rng * math.cos(elev) * math.sin(azim), rng * math.cos(elev) * math.cos(azim),
                               rng * math.sin(elev), d * dopp_u, s * snr_u])
            out['pointCloud'] = points
            out['numDetectedPoints'] = len(points)
        elif tlv_type == 1010:
            out['trackData'] = [list(TARGET.unpack_from(payload, i * TARGET.size)) for i in range(length // TARGET.size)]
        elif tlv_type == 1012:
            out['heightData'] = [list(HEIGHT.unpack_from(payload, i * HEIGHT.size)) for i in range(length // HEIGHT.size)]
        elif tlv_type == 1021 and length >= 4:
            out['presence'] = struct.unpack_from('<I', payload)[0]
    return out
