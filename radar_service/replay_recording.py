"""Replay a labelled recording through the fall rule in config.json and print what it would alert.

Usage: python replay_recording.py recordings/fall_01.jsonl [more.jsonl ...]
Nothing is written to the real event database (a temporary one is used).
"""
import json
import sys
import tempfile
from pathlib import Path

import service

ROOT = Path(__file__).resolve().parent


def replay(path, fall_config):
    rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    frames = [r for r in rows if r.get('type') == 'frame']
    with tempfile.TemporaryDirectory() as tmp:
        service.ROOT = Path(tmp)
        cls = service.FloorDropDetector if fall_config.get('rule') == 'floor_drop' else service.FallDetector
        detector = cls(dict(fall_config, local_sound=False))
        seen, now = [], [0.0]
        store = detector._store
        detector._store = lambda kind, track, tid, f: (seen.append((now[0], kind, tid, dict(f or {}))), store(kind, track, tid, f))
        for r in frames:
            now[0] = r['t']
            tracks = [dict(t, vx_mps=t.get('vx_mps', 0), vy_mps=t.get('vy_mps', 0), vz_mps=t.get('vz_mps', 0),
                           height_min_m=0) for t in r['tracks']]
            for t in tracks:
                for key in ('fall_state', 'reference_m', 'height_filtered_m', 'low_seconds', 'fall_check'):
                    t.pop(key, None)
            detector.update(tracks, r['t'])
    print('%s: %d frames, %.0f s' % (Path(path).name, len(frames), frames[-1]['t'] if frames else 0))
    for t, kind, tid, d in seen:
        print('   %-18s track %-3s at %6.1f s  drop %s m in %s s, low %s m, radar lost %s s' % (
            kind, tid, t, d.get('drop_m'), d.get('duration_s'), d.get('low_h_m'), d.get('radar_lost_s')))


if __name__ == '__main__':
    config = service.load_fall_config(json.loads((ROOT / 'config.json').read_text(encoding='utf-8-sig'))['fall'])
    for p in sys.argv[1:]:
        replay(p, config)
