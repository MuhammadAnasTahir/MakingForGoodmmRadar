"""Local radar gateway for TI Radar Toolbox 4.30, Python 3.9+."""
import argparse
import json
import logging
import math
import mimetypes
from urllib.parse import urlsplit, unquote
from pathlib import Path
import sqlite3
import struct
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

mimetypes.add_type('font/woff2', '.woff2')
MAGIC = b'\x02\x01\x04\x03\x06\x05\x08\x07'
ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()
STATE = {'status': 'starting', 'tracks': [], 'last_frame_at': None, 'error': None}
STOP = threading.Event()
PUBLIC_INFO = {}
FILTER_LOCK = threading.Lock()
FILTERS = {'enabled': False, 'roi': [-3.0, 3.0, 0.0, 6.0], 'exclusions': []}


def validate_box(v):
    if not isinstance(v, list) or len(v) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in v):
        raise ValueError('Each rectangle needs four finite numbers: xmin,xmax,ymin,ymax')
    if v[0] >= v[1] or v[2] >= v[3]:
        raise ValueError('Rectangle minimum must be less than maximum')
    return list(v)


def validate_filters(value):
    if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
        raise ValueError('enabled must be boolean')
    box = validate_box
    zones = value.get('exclusions', [])
    if not isinstance(zones, list) or len(zones) > 50:
        raise ValueError('At most 50 exclusion rectangles')
    return dict(enabled=value['enabled'], roi=box(value.get('roi')), exclusions=[box(v) for v in zones])


def filter_tracks(tracks, settings):
    def inside(t, b):
        return b[0] <= t['x_m'] <= b[1] and b[2] <= t['y_m'] <= b[3]
    if not settings['enabled']:
        return tracks
    return [t for t in tracks if inside(t, settings['roi']) and not any(inside(t, b) for b in settings['exclusions'])]



@contextmanager
def database():
    db = sqlite3.connect(str(ROOT / 'events.sqlite3'), timeout=10)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, timestamp REAL, track_id INTEGER, details TEXT, acknowledged INTEGER DEFAULT 0, kind TEXT NOT NULL DEFAULT 'fall')")
            # Safe migration for databases created before event kinds existed: old rows become 'fall'.
            if 'kind' not in {row[1] for row in db.execute('PRAGMA table_info(events)')}:
                db.execute("ALTER TABLE events ADD COLUMN kind TEXT NOT NULL DEFAULT 'fall'")
            yield db
    finally:
        db.close()


# Defaults for every key in config.json "fall". Values in config.json override these.
FALL_DEFAULTS = dict(
    window_seconds=2.5,          # legacy key from the old rule; kept for compatibility, no longer used
    height_ratio=0.6,            # drop candidate: filtered height < ratio * reference height
    confirm_seconds=1.0,         # must still be low this long after the drop
    cooldown_seconds=30,         # per track ID, between alerts
    local_sound=False,
    recovery_ratio=0.85,         # latch release: back to this share of reference ...
    recovery_seconds=2.0,        # ... for this long
    min_drop_m=0.6,              # big drop: reference - low height
    fall_end_fraction=0.9,       # drop duration is timed from 10% to this share of the drop
    max_low_height_m=0.7,        # big drop: low height must be at most this
    fall_max_duration_s=1.2,     # fast drop: 10%->90% of the drop within this
    fall_min_speed_mps=1.0,      # fast drop: peak descent speed at least this
    floor_alert_s=30.0,          # slow descent still low outside safe zones -> prolonged_low
    safe_zones=[],               # [[xmin,xmax,ymin,ymax], ...] e.g. bed; no alerts inside
    rise_ratio=0.7,              # back above ratio * reference -> person got up (hysteresis)
    reference_seconds=6.0,       # history used for the reference height
    reference_percentile=0.8,    # reference = this percentile of filtered heights
    speed_span_s=0.2,            # descent speed measured over spans of this length
    median_frames=3,             # median filter length on height_max_m
    track_timeout_s=3.0,         # keep detector state for a track missing this long
    rule='drop_profile',         # 'drop_profile' (FallDetector) or 'floor_drop' (FloorDropDetector)
    upright_height_m=1.1,        # floor_drop: at least this tall = upright (standing or on a chair)
    handoff_radius_m=2.0,        # floor_drop: a new track this close to a just-lost one continues it
    handoff_seconds=3.0,         # floor_drop: ... if it appears within this many seconds
)
ALERT_KINDS = ('fall', 'prolonged_low')
MIN_REFERENCE_HISTORY_S = 1.0   # need this much history before trusting the reference height
SETTLE_MAX_WAIT_S = 3.0         # wait at most this long after confirm_seconds for the drop to settle
LOW_WINDOW_S = 0.5              # low height = median of the last 0.5 s


def _percentile(values, p):
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _median(values):
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


class _FallTrack:
    def __init__(self, median_frames):
        self.raw = deque(maxlen=max(1, int(median_frames)))
        self.hist = deque()        # (t, filtered height): long enough to measure a drop onset
        self.ref_hist = deque()    # (t, filtered height): reference_seconds only
        self.state = 'upright'     # upright -> candidate -> low / pending -> (alert, latched)
        self.reference = None
        self.t_cross = None
        self.min_since_cross = None
        self.big = False
        self.kind = None
        self.features = None
        self.last_seen = 0.0
        self.x = self.y = 0.0


class FallDetector:
    """Fall vs. intentional lying down from height_max_m: drop magnitude, speed and duration.

    A fall alert needs a big drop, a fast drop, staying low for confirm_seconds and a
    position outside safe_zones. Slow big drops are logged as info only, unless the person
    stays on the floor outside safe zones for floor_alert_s (prolonged_low alert).
    Heuristic; requires real-world validation.
    """
    def __init__(self, config):
        self.config = dict(FALL_DEFAULTS, **config)
        self.history = {}      # track_id -> _FallTrack
        self.cooldown = {}
        self.latched = {}

    def update(self, tracks, now):
        active = {t['track_id'] for t in tracks}
        for tid, episode in self.latched.items():
            if tid not in active:
                episode['recovery_since'] = None
                episode['last_recovery_at'] = None
        for tid in list(self.history):
            if tid not in active and now - self.history[tid].last_seen > self.config['track_timeout_s']:
                del self.history[tid]
        for track in tracks:
            tid = track['track_id']
            height = track['height_max_m']
            track['fall_state'] = 'height_unavailable'
            if height is None or not math.isfinite(height) or height <= 0:
                # Skip the sample; a single missing height must not erase a drop in progress.
                if tid in self.latched:
                    self.latched[tid]['recovery_since'] = None
                    self.latched[tid]['last_recovery_at'] = None
                continue
            episode = self.latched.get(tid)
            if episode is not None:
                track['fall_state'] = 'suspected_fall'
                if height >= self.config['recovery_ratio'] * episode['reference']:
                    last = episode['last_recovery_at']
                    if last is None or now - last > 0.5:
                        episode['recovery_since'] = now
                    episode['last_recovery_at'] = now
                    if now - episode['recovery_since'] >= self.config['recovery_seconds']:
                        self.latched.pop(tid, None)
                        self.history.pop(tid, None)
                        track['fall_state'] = 'tracking'
                        logging.info('Fall detector rearmed: track=%s', tid)
                else:
                    episode['recovery_since'] = None
                    episode['last_recovery_at'] = None
                continue
            self._detect(track, tid, height, now)

    # ------------------------------------------------------------------ internals
    def _detect(self, track, tid, height, now):
        c = self.config
        tr = self.history.get(tid)
        if tr is None:
            tr = self.history[tid] = _FallTrack(c['median_frames'])
        tr.last_seen, tr.x, tr.y = now, track['x_m'], track['y_m']
        tr.raw.append(height)
        hf = _median(tr.raw)
        tr.hist.append((now, hf))
        tr.ref_hist.append((now, hf))
        keep = max(c['reference_seconds'], c['floor_alert_s']) + 5.0
        while tr.hist and now - tr.hist[0][0] > keep:
            tr.hist.popleft()
        while tr.ref_hist and now - tr.ref_hist[0][0] > c['reference_seconds']:
            tr.ref_hist.popleft()
        track['height_filtered_m'] = round(hf, 3)

        if tr.state == 'upright':
            track['fall_state'] = 'tracking'
            if now - tr.ref_hist[0][0] < MIN_REFERENCE_HISTORY_S:
                return
            reference = _percentile([v for _, v in tr.ref_hist], c['reference_percentile'])
            track['reference_m'] = round(reference, 3)
            if hf < c['height_ratio'] * reference and reference - hf >= 0.5 * c['min_drop_m']:
                tr.state, tr.reference, tr.t_cross, tr.min_since_cross = 'candidate', reference, now, hf
                tr.big, tr.kind, tr.features = False, None, None
            else:
                return

        track['reference_m'] = round(tr.reference, 3)
        if tr.features:
            track['fall_check'] = tr.features
        risen = hf > c['rise_ratio'] * tr.reference

        if tr.state == 'candidate':
            track['fall_state'] = 'suspected_fall'
            tr.min_since_cross = min(tr.min_since_cross, hf)
            if risen:
                low = tr.min_since_cross
                self._store('crouch_rejected', track, tid, dict(
                    drop_m=round(tr.reference - low, 3), duration_s=round(now - tr.t_cross, 2),
                    low_h_m=round(low, 3), reference_m=round(tr.reference, 3)))
                self._reset(tr, track)
                return
            if now - tr.t_cross < c['confirm_seconds']:
                return
            if not self._settled(tr, now) and now - tr.t_cross < c['confirm_seconds'] + SETTLE_MAX_WAIT_S:
                return   # measure the drop only when it is complete (a slow lie-down is still moving)
            self._classify(tr, track, tid, now)
            if tr.state != 'pending':
                return

        if tr.state == 'low':
            track['fall_state'] = 'low'
            if risen:
                self._reset(tr, track)
                return
            track['low_seconds'] = round(now - tr.t_cross, 1)
            if not tr.big:
                # Staged descent (e.g. kneel, then lie down): judge again once the drop is big
                # and the person has stopped going down, so the whole descent is timed.
                if not self._settled(tr, now):
                    return
                self._classify(tr, track, tid, now)
                if tr.state != 'pending':
                    return
            elif now - tr.t_cross >= c['floor_alert_s'] and not self._in_safe_zone(tr):
                recent = [v for ts, v in tr.hist if ts >= now - c['confirm_seconds']]
                low_now = _median(recent)
                if low_now > c['max_low_height_m']:
                    return
                tr.state, tr.kind = 'pending', 'prolonged_low'
                tr.features = dict(tr.features or {}, low_h_m=round(low_now, 3),
                                   drop_m=round(tr.reference - low_now, 3),
                                   low_duration_s=round(now - tr.t_cross, 1))
            else:
                return

        # state == 'pending': alert decided, waiting only for the per-track cooldown.
        track['fall_state'] = 'suspected_fall'
        if risen:
            self._reset(tr, track)
            return
        if now < self.cooldown.get(tid, 0):
            return
        self._store(tr.kind, track, tid, tr.features)
        self.cooldown[tid] = now + c['cooldown_seconds']
        self.latched[tid] = dict(reference=tr.reference, recovery_since=None, last_recovery_at=None)
        self.history.pop(tid, None)
        logging.warning('SUSPECTED %s track=%s %s: inspect and acknowledge on the local page',
                        tr.kind.upper(), tid, json.dumps(tr.features))
        if c.get('local_sound') and sys.platform == 'win32':
            import winsound
            winsound.PlaySound('SystemExclamation', winsound.SND_ALIAS | winsound.SND_ASYNC)

    def _classify(self, tr, track, tid, now):
        c = self.config
        feats = self._features(tr, now)
        tr.features = feats
        track['fall_check'] = feats
        big = feats['drop_m'] >= c['min_drop_m'] and feats['low_h_m'] <= c['max_low_height_m']
        fast = feats['duration_s'] <= c['fall_max_duration_s'] and feats['peak_speed_mps'] >= c['fall_min_speed_mps']
        safe = self._in_safe_zone(tr)
        tr.big = big
        if big and fast and not safe:
            tr.state, tr.kind = 'pending', 'fall'
            return
        tr.state = 'low'
        if big:
            self._store('lie_down_rejected', track, tid, dict(feats, reason='safe_zone' if safe else 'slow'))

    def _settled(self, tr, now):
        """Height went down less than 0.1 m over the last second."""
        earlier = [v for ts, v in tr.hist if now - 1.2 <= ts <= now - 0.8]
        latest = [v for ts, v in tr.hist if ts >= now - 0.3]
        return bool(earlier and latest) and _median(earlier) - _median(latest) < 0.1

    def _features(self, tr, now):
        c = self.config
        pts = list(tr.hist)
        recent = [v for ts, v in pts if ts >= now - min(c['confirm_seconds'], LOW_WINDOW_S)]
        low = _median(recent)
        reference = tr.reference
        drop = reference - low
        lvl10, lvl90 = reference - 0.1 * drop, reference - c['fall_end_fraction'] * drop
        t10 = None
        for ts, v in pts:
            if ts > tr.t_cross:
                break
            if v >= lvl10:
                t10 = ts
        if t10 is None:
            # Drop onset is older than the kept history: certainly not a fast drop.
            t10 = pts[0][0]
            duration = max(now - t10, c['fall_max_duration_s'] + 1.0)
        else:
            t90 = next((ts for ts, v in pts if ts >= t10 and v <= lvl90), now)
            duration = t90 - t10
        span = c['speed_span_s']
        peak, j = 0.0, 0
        for i, (ti, hi) in enumerate(pts):
            if ti < t10 - span:
                continue
            if ti > t10 + duration + span:
                break
            j = max(j, i + 1)
            while j < len(pts) and pts[j][0] < ti + span - 1e-6:
                j += 1
            if j >= len(pts):
                break
            tj, hj = pts[j]
            peak = max(peak, (hi - hj) / (tj - ti))
        return dict(drop_m=round(drop, 3), duration_s=round(duration, 2), peak_speed_mps=round(peak, 2),
                    low_h_m=round(low, 3), reference_m=round(reference, 3))

    def _in_safe_zone(self, tr):
        return any(z[0] <= tr.x <= z[1] and z[2] <= tr.y <= z[3] for z in self.config['safe_zones'])

    def _reset(self, tr, track):
        tr.state, tr.reference, tr.t_cross, tr.big, tr.kind, tr.features = 'upright', None, None, False, None, None
        track['fall_state'] = 'tracking'
        track.pop('low_seconds', None)

    def _store(self, kind, track, tid, features):
        details = dict(track, **(features or {}))
        details['kind'] = kind
        with database() as db:
            db.execute('INSERT INTO events(timestamp,track_id,kind,details) VALUES(?,?,?,?)',
                       (time.time(), tid, kind, json.dumps(details, allow_nan=False)))
        if kind not in ALERT_KINDS:
            logging.info('Fall rule info %s track=%s %s', kind, tid, json.dumps(features))


class _FloorTrack:
    def __init__(self, median_frames):
        self.raw = deque(maxlen=max(1, int(median_frames)))
        self.hist = deque()           # (t, filtered height)
        self.state = 'upright'        # upright -> low -> (alerted)
        self.fast = False
        self.low_since = None
        self.features = None
        self.blind_s = 0.0            # time the radar lost this person (handoff), not counted as falling
        self.up_seen = False
        self.x = self.y = 0.0
        self.last_seen = 0.0
        self.recovery_since = None


class FloorDropDetector(FallDetector):
    """Simple fall rule for the TI height output, which reads a person on the floor at 0.05-0.6 m.

    Fall: from upright (>= upright_height_m) to the floor (<= max_low_height_m) within
    fall_max_duration_s, then still on the floor confirm_seconds later. Slower descents only
    alert after floor_alert_s (prolonged_low). When the radar loses a person and a new track
    appears nearby shortly after, the new track continues the old one's history.
    """
    def __init__(self, config):
        super().__init__(config)
        self.floor = {}

    def update(self, tracks, now):
        c = self.config
        active = {t['track_id'] for t in tracks}
        for tid in [k for k, v in self.floor.items() if k not in active and now - v.last_seen > c['handoff_seconds'] + 5]:
            del self.floor[tid]
            self.latched.pop(tid, None)
        for track in tracks:
            tid = track['track_id']
            tr = self.floor.get(tid)
            if tr is None:
                tr = self._handoff(track, active, now) or _FloorTrack(c['median_frames'])
                self.floor[tid] = tr
            tr.x, tr.y, tr.last_seen = track['x_m'], track['y_m'], now
            self._step(track, tid, tr, now)

    def _handoff(self, track, active, now):
        c, best, best_d = self.config, None, None
        for old, tr in self.floor.items():
            if old in active or now - tr.last_seen > c['handoff_seconds']:
                continue
            d = math.hypot(track['x_m'] - tr.x, track['y_m'] - tr.y)
            if d <= c['handoff_radius_m'] and (best_d is None or d < best_d):
                best, best_d = old, d
        if best is None:
            return None
        tr = self.floor.pop(best)
        tr.blind_s += now - tr.last_seen
        tr.raw.clear()
        if best in self.latched:
            self.latched[track['track_id']] = self.latched.pop(best)
        if best in self.cooldown:
            self.cooldown[track['track_id']] = self.cooldown[best]
        logging.info('Track %s continues lost track %s (%.1f m, %.1f s)', track['track_id'], best, best_d, now - tr.last_seen)
        return tr

    def _step(self, track, tid, tr, now):
        c = self.config
        h = track.get('height_max_m')
        track['fall_state'] = 'height_unavailable'
        if h is None or not math.isfinite(h) or h <= 0:
            return
        tr.raw.append(h)
        hf = _median(tr.raw)
        tr.hist.append((now, hf))
        while tr.hist and now - tr.hist[0][0] > 40:
            tr.hist.popleft()
        track['height_filtered_m'] = round(hf, 3)
        up, low_m = c['upright_height_m'], c['max_low_height_m']
        if hf >= up:
            tr.up_seen = True
        ups = [v for ts, v in tr.hist if v >= up and now - ts <= 8.0]   # recent upright heights only
        if ups:
            track['reference_m'] = round(_percentile(ups, 0.8), 3)

        if tr.state == 'alerted':
            track['fall_state'] = 'suspected_fall'
            if hf >= up:
                tr.recovery_since = tr.recovery_since or now
                if now - tr.recovery_since >= c['recovery_seconds']:
                    tr.state, tr.recovery_since, tr.blind_s = 'upright', None, 0.0
                    self.latched.pop(tid, None)
                    track['fall_state'] = 'tracking'
                    logging.info('Fall detector rearmed: track=%s', tid)
            else:
                tr.recovery_since = None
            return

        if tr.state == 'upright':
            track['fall_state'] = 'tracking'
            if hf > low_m:
                return
            tr.state, tr.low_since = 'low', now
            if not ups:
                tr.fast, tr.features = False, None     # never seen upright: no drop to judge
            else:
                # Time the whole descent: from leaving the person's usual upright height to the floor.
                h_up = _percentile(ups, 0.8)
                start_level = max(up, h_up - 0.15)
                t_up = next((ts for ts, v in reversed(tr.hist) if v >= start_level), None)
                if t_up is None:
                    t_up = next(ts for ts, v in reversed(tr.hist) if v >= up)
                duration = max(0.0, now - t_up - tr.blind_s)
                tr.fast = duration <= c['fall_max_duration_s']
                tr.features = dict(drop_m=round(h_up - hf, 3), duration_s=round(duration, 2),
                                   low_h_m=round(hf, 3), reference_m=round(h_up, 3), radar_lost_s=round(tr.blind_s, 1))
                if not tr.fast:
                    self._store('lie_down_rejected', track, tid, dict(tr.features, reason='slow'))

        # state == 'low'
        safe = self._in_safe_zone(tr)
        track['fall_state'] = 'suspected_fall' if tr.fast and not safe else 'low'
        track['low_seconds'] = round(now - tr.low_since, 1)
        if tr.features:
            track['fall_check'] = tr.features
        if hf >= low_m + 0.25:                     # got up again
            if tr.fast and now - tr.low_since < c['confirm_seconds']:
                lows = [v for ts, v in tr.hist if ts >= tr.low_since]
                self._store('crouch_rejected', track, tid, dict(tr.features, low_h_m=round(min(lows), 3)))
            tr.state, tr.fast, tr.features, tr.blind_s = 'upright', False, None, 0.0
            track['fall_state'] = 'tracking'
            track.pop('low_seconds', None)
            track.pop('fall_check', None)
            return
        if safe:
            return
        kind = None
        if tr.fast and now - tr.low_since >= c['confirm_seconds']:
            kind = 'fall'
        elif tr.up_seen and now - tr.low_since >= c['floor_alert_s']:
            kind = 'prolonged_low'
        if kind is None or now < self.cooldown.get(tid, 0):
            return
        if kind == 'prolonged_low':
            tr.features = dict(tr.features or {}, low_duration_s=round(now - tr.low_since, 1), low_h_m=round(hf, 3))
        self._store(kind, track, tid, tr.features)
        self.cooldown[tid] = now + c['cooldown_seconds']
        self.latched[tid] = dict(kind=kind)
        tr.state, tr.recovery_since = 'alerted', None
        track['fall_state'] = 'suspected_fall'
        logging.warning('SUSPECTED %s track=%s %s: inspect and acknowledge on the local page', kind.upper(), tid, json.dumps(tr.features))
        if c.get('local_sound') and sys.platform == 'win32':
            import winsound
            winsound.PlaySound('SystemExclamation', winsound.SND_ALIAS | winsound.SND_ASYNC)


def packets(port):
    buffer = bytearray()
    last_packet = time.monotonic()
    while not STOP.is_set():
        buffer.extend(port.read(min(max(port.in_waiting, 1), 65536)))
        while True:
            index = buffer.find(MAGIC)
            if index < 0:
                buffer[:] = buffer[-7:]
                break
            del buffer[:index]
            if len(buffer) < 16:
                break
            size = struct.unpack_from('<I', buffer, 12)[0]
            if size < 40 or size > 1024 * 1024:
                del buffer[0]
                continue
            if len(buffer) < size:
                break
            packet = bytes(buffer[:size])
            del buffer[:size]
            last_packet = time.monotonic()
            yield packet
        if time.monotonic() - last_packet > 6:
            raise TimeoutError('No complete radar frame for 6 seconds; check power, mode, ports and CFG')


def already_streaming(port, seconds=2.0):
    """True if radar frames are arriving on the data port."""
    deadline, seen = time.monotonic() + seconds, bytearray()
    while time.monotonic() < deadline:
        seen.extend(port.read(port.in_waiting or 1))
        if MAGIC in seen:
            return True
        seen[:] = seen[-7:]
    return False


def send_config(port, path):
    port.write(b'\n')
    time.sleep(0.2)
    port.reset_input_buffer()
    commands = [line.split('%', 1)[0].strip() for line in path.read_text(encoding='utf-8-sig').splitlines()]
    for command in filter(None, commands):
        port.write((command + '\n').encode('ascii'))
        port.flush()
        response = bytearray()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            response.extend(port.read(port.in_waiting or 1))
            if b'Done' in response or b'Error' in response or b'not recognized' in response:
                break
        text = response.decode('ascii', errors='replace')
        logging.info('CLI %s -> %s', command, text.strip())
        if b'Done' not in response:
            raise RuntimeError('CFG command failed: ' + command + ' | ' + text)
        time.sleep(0.05)


class MotionClassifier:
    # Heuristic retained from the installed version; not human recognition.
    def __init__(self):
        self.targets = {}

    def update(self, tracks, now):
        for tid in list(self.targets):
            if now - self.targets[tid]['last_seen'] > 3:
                del self.targets[tid]
        visible = []
        for t in tracks:
            position = (t['x_m'], t['y_m'], t['z_m'])
            target = self.targets.setdefault(t['track_id'], dict(first_seen=now, last_seen=now, anchor=position, confirmed=False))
            target['last_seen'] = now
            if math.sqrt(sum((a-b)**2 for a,b in zip(position,target['anchor']))) >= 0.25:
                target['confirmed'] = True
            if target['confirmed']:
                t['motion_state'] = 'motion_confirmed'
            elif now-target['first_seen'] < 30:
                t['motion_state'] = 'unconfirmed'
            else:
                t['motion_state'] = 'suspected_static'
                continue
            visible.append(t)
        return visible


POSTURE_DEFAULTS = dict(
    window_s=300.0,          # standing reference: this much history per person
    percentile=0.9,          # reference = this percentile of the person's heights (their own standing height)
    min_history_s=2.0,       # need this much history before judging sitting vs. standing
    upright_ratio=0.8,       # at least this share of the reference -> standing / walking
    lying_height_m=0.75,     # at most this height (m) -> lying / on the floor
    walk_speed_mps=0.35,     # moved at least this fast over the last second -> walking
    hold_s=0.6,              # a new posture must hold this long before it is shown
    walk_hold_s=1.0,         # standing <-> walking must hold this long (avoids flicker)
)
POSTURES = ('standing', 'walking', 'sitting', 'lying', 'fallen')


class PostureTracker:
    """Display label per person from height relative to their own standing height and speed.

    For the page only; the fall alerts come from FallDetector. Works for any visitor height
    because sitting is judged as a share of that person's own reference height.
    """
    def __init__(self, config=None):
        self.config = dict(POSTURE_DEFAULTS, **(config or {}))
        self.tracks = {}

    def update(self, tracks, now, latched=()):
        c = self.config
        for tid in [k for k, v in self.tracks.items() if now - v['seen'] > 5.0]:
            del self.tracks[tid]
        for track in tracks:
            tid = track['track_id']
            st = self.tracks.setdefault(tid, dict(hist=deque(), pos=deque(), shown=None, cand=None, since=now, seen=now))
            st['seen'] = now
            h = track.get('height_filtered_m', track.get('height_max_m'))
            valid = h is not None and math.isfinite(h) and h > 0
            if valid:
                st['hist'].append((now, h))
            while st['hist'] and now - st['hist'][0][0] > c['window_s']:
                st['hist'].popleft()
            st['pos'].append((now, track['x_m'], track['y_m']))
            while st['pos'] and now - st['pos'][0][0] > 1.0:
                st['pos'].popleft()
            t0, x0, y0 = st['pos'][0]
            speed = math.hypot(track['x_m'] - x0, track['y_m'] - y0) / (now - t0) if now - t0 >= 0.5 else 0.0
            ref = None
            if st['hist'] and now - st['hist'][0][0] >= c['min_history_s']:
                ref = _percentile([v for _, v in st['hist']], c['percentile'])
            if tid in latched:
                raw = 'fallen'
            elif not valid or ref is None:
                raw = st['shown'] or ('walking' if speed >= c['walk_speed_mps'] else 'standing')
            elif h <= c['lying_height_m']:
                raw = 'lying'
            elif h < c['upright_ratio'] * ref:
                raw = 'sitting'
            else:
                raw = 'walking' if speed >= c['walk_speed_mps'] else 'standing'
            if st['shown'] is None or raw == 'fallen':
                st['shown'], st['cand'] = raw, None
            elif raw == st['shown']:
                st['cand'] = None
            elif raw != st['cand']:
                st['cand'], st['since'] = raw, now
            else:
                hold = c['walk_hold_s'] if {raw, st['shown']} == {'standing', 'walking'} else c['hold_s']
                if now - st['since'] >= hold:
                    st['shown'], st['cand'] = raw, None
            track['posture'] = st['shown']
            if ref:
                track['posture_reference_m'] = round(ref, 3)
            track['walk_speed_mps'] = round(speed, 2)


def limit_room(tracks, box):
    """Drop tracks outside the fixed room limit (removes ghost targets far from the radar)."""
    if not box:
        return tracks
    return [t for t in tracks if box[0] <= t['x_m'] <= box[1] and box[2] <= t['y_m'] <= box[3]]


# ---------------------------------------------------------------- labelled test recordings
RECORD_LABELS = ('walk', 'stand', 'sit_down', 'stand_up', 'bend', 'lie_slow', 'lie_stay', 'fall',
                 'fall_get_up', 'two_people', 'offline', 'other')
RECORD_MAX_S = 300.0
RECORD_LOCK = threading.Lock()
RECORDING = {}


def _record_status_locked():
    if not RECORDING:
        return dict(active=False, labels=list(RECORD_LABELS), last=RECORDING_LAST.get('name'))
    return dict(active=True, labels=list(RECORD_LABELS), name=RECORDING['name'], label=RECORDING['label'],
                seconds=round(time.time() - RECORDING['started'], 1), frames=RECORDING['frames'],
                last=RECORDING_LAST.get('name'))


RECORDING_LAST = {}


def record_status():
    with RECORD_LOCK:
        return _record_status_locked()


def start_recording(label):
    if label not in RECORD_LABELS:
        raise ValueError('Unknown label: ' + str(label))
    with RECORD_LOCK:
        if RECORDING:
            _stop_recording_locked()
        folder = ROOT / 'recordings'
        folder.mkdir(exist_ok=True)
        numbers = [int(p.stem.rsplit('_', 1)[1]) for p in folder.glob(label + '_*.jsonl')
                   if p.stem.rsplit('_', 1)[1].isdigit() and p.stem.rsplit('_', 1)[0] == label]
        name = '%s_%02d' % (label, max(numbers, default=0) + 1)
        handle = (folder / (name + '.jsonl')).open('w', encoding='utf-8')
        started = time.time()
        handle.write(json.dumps(dict(type='start', label=label, name=name, started_at=started,
                                     started_local=time.strftime('%Y-%m-%d %H:%M:%S'),
                                     fall=PUBLIC_INFO.get('fall'), room_limit=PUBLIC_INFO.get('room_limit'))) + '\n')
        RECORDING.update(label=label, name=name, handle=handle, started=started, frames=0,
                         last_posture={}, changes=[], track_ids=set())
        logging.info('Recording started: %s', name)
        return _record_status_locked()


def record_frame(tracks, extra):
    with RECORD_LOCK:
        if not RECORDING:
            return
        t = round(time.time() - RECORDING['started'], 3)
        keep = ('track_id', 'x_m', 'y_m', 'z_m', 'vx_mps', 'vy_mps', 'vz_mps', 'height_max_m', 'height_filtered_m',
                'reference_m', 'fall_state', 'low_seconds', 'posture', 'posture_reference_m', 'walk_speed_mps',
                'height_pts_m', 'points_near')
        rows = [{k: tr[k] for k in keep if k in tr} for tr in tracks]
        RECORDING['handle'].write(json.dumps(dict(type='frame', t=t, tracks=rows, **extra)) + '\n')
        RECORDING['frames'] += 1
        for tr in tracks:
            RECORDING['track_ids'].add(tr['track_id'])
            p = tr.get('posture')
            if p and RECORDING['last_posture'].get(tr['track_id']) != p:
                RECORDING['last_posture'][tr['track_id']] = p
                RECORDING['changes'].append([t, tr['track_id'], p])
        if t > RECORD_MAX_S:
            _stop_recording_locked()


def stop_recording():
    with RECORD_LOCK:
        if RECORDING:
            _stop_recording_locked()
        return _record_status_locked()


def _stop_recording_locked():
    rec = dict(RECORDING)
    RECORDING.clear()
    ended = time.time()
    try:
        with database() as db:
            rows = db.execute('SELECT id,timestamp,track_id,kind,details FROM events WHERE timestamp>=? ORDER BY id',
                              (rec['started'],)).fetchall()
        evs = [dict(id=r[0], t=round(r[1] - rec['started'], 2), track_id=r[2], kind=r[3], details=json.loads(r[4])) for r in rows]
    except Exception as exc:   # the recording must still close cleanly
        evs = [dict(error=str(exc))]
    summary = dict(type='summary', label=rec['label'], name=rec['name'], duration_s=round(ended - rec['started'], 1),
                   frames=rec['frames'], track_ids=sorted(rec['track_ids']), posture_changes=rec['changes'],
                   events=evs, alerts=[e.get('kind') for e in evs if e.get('kind') in ALERT_KINDS])
    rec['handle'].write(json.dumps(summary) + '\n')
    rec['handle'].close()
    RECORDING_LAST.update(name=rec['name'], summary=summary)
    logging.info('Recording saved: %s (%s frames, alerts %s)', rec['name'], rec['frames'], summary['alerts'])


def point_heights(tracks, cloud, sensor_position, radius=0.6, min_points=3):
    """Height of each person from the raw points near them (world metres above the floor).

    The TI height output lags and freezes when a person lies still, so this measures the
    top of the body directly from this frame's points. Sets height_pts_m / points_near.
    """
    h, _, tilt = sensor_position
    c, s = math.cos(math.radians(tilt)), math.sin(math.radians(tilt))
    world = []
    for row in cloud:
        xs, ys, zs = row[0], row[1], row[2]
        world.append((xs, ys * c + zs * s, h + zs * c - ys * s))
    for track in tracks:
        zs = sorted(z for x, y, z in world
                    if (x - track['x_m']) ** 2 + (y - track['y_m']) ** 2 <= radius * radius and -0.3 <= z <= 2.6)
        track['points_near'] = len(zs)
        track['height_pts_m'] = round(zs[int(0.9 * (len(zs) - 1))], 3) if len(zs) >= min_points else None


def prepare_points(frame):
    points = []
    cloud = frame.get('pointCloud', [])
    count = min(int(frame.get('numDetectedPoints', len(cloud))), len(cloud))
    stride = max(1, math.ceil(count / 2000))
    for row in cloud[:count:stride]:
        values = [float(v) for v in row[:4]]
        if len(values) >= 3 and all(math.isfinite(v) for v in values):
            points.append(values)
    return points, count


def radar_worker(config, parse_frame):
    import serial
    while not STOP.is_set():
        detector = (FloorDropDetector if config['fall'].get('rule') == 'floor_drop' else FallDetector)(config['fall'])
        classifier = MotionClassifier()
        posture = PostureTracker(config.get('posture'))
        try:
            with LOCK:
                STATE.update(status='configuring', tracks=[], points=[], last_frame_at=None, error=None)
            with serial.Serial(config['cli_port'], 115200, timeout=0.15, write_timeout=2) as cli:
                with serial.Serial(config['data_port'], config['data_baud'], timeout=0.2) as data:
                    data.reset_input_buffer()
                    # The radar accepts its cfg once per power-up, and resending it to a running radar
                    # stops it. If frames are already arriving (backend restarted), keep the radar as is.
                    if already_streaming(data):
                        logging.warning('Radar was already running; using its current configuration. '
                                        'Replug the radar to apply changes to the .cfg file.')
                    else:
                        send_config(cli, Path(config['cfg_path']))
                    for packet in packets(data):
                        frame = parse_frame(packet, config['demo'])
                        if frame.get('error'):
                            logging.warning('Dropping frame with parser error %s', frame['error'])
                            continue
                        heights = {int(h[0]): h for h in frame.get('heightData', [])}
                        tracks = []
                        for row in frame.get('trackData', []):
                            values = [float(v) for v in row[1:7]]
                            if not all(math.isfinite(v) for v in values):
                                continue
                            tid = int(row[0])
                            h = heights.get(tid)
                            tracks.append(dict(track_id=tid, x_m=values[0], y_m=values[1], z_m=values[2],
                                               vx_mps=values[3], vy_mps=values[4], vz_mps=values[5],
                                               height_max_m=float(h[1]) if h is not None and math.isfinite(h[1]) else None,
                                               height_min_m=float(h[2]) if h is not None and math.isfinite(h[2]) else None))
                        raw_count = len(tracks)
                        with FILTER_LOCK:
                            settings = dict(FILTERS)
                        tracks = filter_tracks(limit_room(tracks, config.get('room_limit')), settings)
                        region_count = len(tracks)
                        now = time.monotonic()
                        tracks = classifier.update(tracks, now) if config.get('motion_filter', {}).get('enabled', True) else tracks
                        point_heights(tracks, frame.get('pointCloud', []), config.get('expected_sensor_position') or [0, 0, 0])
                        detector.update(tracks, now)
                        posture.update(tracks, now, detector.latched)
                        record_frame(tracks, dict(frame=int(frame.get('frameNum', 0)), raw_count=raw_count))
                        points, point_count = prepare_points(frame)
                        with LOCK:
                            STATE.update(status='streaming', tracks=tracks, last_frame_at=time.time(),
                                         frame_number=int(frame.get('frameNum', 0)), error=None,
                                         raw_track_count=raw_count, filtered_track_count=raw_count-len(tracks),
                                         region_filtered_count=raw_count-region_count, motion_filtered_count=region_count-len(tracks),
                                         points=points, point_count=point_count, point_coordinates='sensor',
                                         height_source='TI target height estimate; not head height')
        except Exception as exc:
            logging.exception('Radar connection failed; retrying in 5 seconds')
            with LOCK:
                STATE.update(status='disconnected', tracks=[], points=[], error=str(exc))
            STOP.wait(5)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, value, status=200):
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == '/api/filters':
            with FILTER_LOCK:
                settings = dict(FILTERS)
            return self.send_json(settings)
        if self.path == '/api/state':
            with LOCK:
                state = dict(STATE)
            last = state['last_frame_at']
            state['frame_age_seconds'] = None if last is None else time.time() - last
            if last is not None and time.time() - last > 3:
                state.update(status='stale', tracks=[], points=[])
            return self.send_json(state)
        if self.path == '/api/events':
            # Alerts and info records are limited separately, so many info records can never
            # push an unacknowledged alert out of the list.
            alert_sql = "kind IN ('fall','prolonged_low')"
            with database() as db:
                rows = db.execute('SELECT id,timestamp,track_id,acknowledged,details,kind FROM events WHERE ' + alert_sql + ' ORDER BY id DESC LIMIT 100').fetchall()
                rows += db.execute('SELECT id,timestamp,track_id,acknowledged,details,kind FROM events WHERE NOT ' + alert_sql + ' ORDER BY id DESC LIMIT 50').fetchall()
            rows.sort(key=lambda r: r[0], reverse=True)
            return self.send_json([dict(id=r[0], timestamp=r[1], track_id=r[2], acknowledged=bool(r[3]), details=json.loads(r[4]), kind=r[5]) for r in rows])
        if self.path == '/api/info':
            return self.send_json(PUBLIC_INFO)
        if self.path == '/api/record':
            return self.send_json(record_status())
        # Serve only web/. Never expose config, logs, database, or Python source.
        path = unquote(urlsplit(self.path).path)
        relative = 'index.html' if path == '/' else path.lstrip('/')
        webroot = (ROOT / 'web').resolve()
        target = (webroot / relative).resolve()
        if webroot not in target.parents or not target.is_file():
            return self.send_error(404)
        if target.suffix.lower() not in {'.html','.js','.css','.png','.jpg','.jpeg','.svg','.webp','.ico','.woff2'}:
            return self.send_error(404)
        payload = target.read_bytes()
        self.send_response(200)
        mime = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        self.send_header('Content-Type', mime + ('; charset=utf-8' if target.suffix.lower() in {'.html','.js','.css'} else ''))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        if self.path == '/api/filters':
            if self.headers.get('X-Radar-Action') != 'configure':
                return self.send_json({'error': 'Missing action header'}, 403)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 16384:
                    raise ValueError('Invalid request size')
                settings = validate_filters(json.loads(self.rfile.read(size)))
                with FILTER_LOCK:
                    temp = ROOT / 'filters.json.tmp'
                    temp.write_text(json.dumps(settings, indent=2), encoding='utf-8')
                    temp.replace(ROOT / 'filters.json')
                    FILTERS.clear()
                    FILTERS.update(settings)
                return self.send_json({'saved': True, 'filters': settings})
            except (ValueError, TypeError, OSError) as exc:
                return self.send_json({'error': str(exc)}, 400)
        if self.path == '/api/record':
            if self.headers.get('X-Radar-Action') != 'record':
                return self.send_json({'error': 'Missing action header'}, 403)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024:
                    raise ValueError('Invalid request size')
                body = json.loads(self.rfile.read(size))
                if body.get('action') == 'start':
                    return self.send_json(start_recording(body.get('label')))
                if body.get('action') == 'stop':
                    return self.send_json(stop_recording())
                raise ValueError('action must be start or stop')
            except (ValueError, TypeError, AttributeError, OSError) as exc:
                return self.send_json({'error': str(exc)}, 400)
        # Bind to loopback; require a custom header to reject cross-site form submissions.
        if self.headers.get('X-Radar-Action') != 'ack':
            return self.send_json({'error': 'Missing action header'}, 403)
        parts = self.path.strip('/').split('/')
        if len(parts) == 4 and parts[:2] == ['api', 'events'] and parts[3] == 'ack' and parts[2].isdigit():
            with database() as db:
                changed = db.execute('UPDATE events SET acknowledged=1 WHERE id=?', (int(parts[2]),)).rowcount
            return self.send_json({'acknowledged': bool(changed)}, 200 if changed else 404)
        self.send_error(404)

    def log_message(self, *args):
        pass


def load_fall_config(fall):
    """Merge config.json "fall" with defaults and validate the new keys."""
    merged = dict(FALL_DEFAULTS, **fall)
    zones = merged['safe_zones']
    if not isinstance(zones, list) or len(zones) > 50:
        raise ValueError('fall.safe_zones must be a list of at most 50 rectangles')
    try:
        merged['safe_zones'] = [validate_box(z) for z in zones]
    except ValueError as exc:
        raise ValueError('fall.safe_zones: ' + str(exc))
    if not 0.2 <= merged['fall_end_fraction'] <= 0.95:
        raise ValueError('fall.fall_end_fraction must be between 0.2 and 0.95')
    for key in ('min_drop_m', 'max_low_height_m', 'fall_max_duration_s', 'fall_min_speed_mps', 'floor_alert_s',
                'reference_seconds', 'speed_span_s', 'confirm_seconds', 'cooldown_seconds'):
        value = merged[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('fall.' + key + ' must be a positive number')
    return merged


class Server(ThreadingHTTPServer):
    # A browser opens about a dozen connections at once when the page loads. With Python's default
    # queue of 5, Windows refuses the rest, and a refused app.js leaves the page dead.
    request_queue_size = 64
    daemon_threads = True
    # On Windows, address reuse lets a second copy of the service share the port without any error,
    # and the page then flips between the two copies. Refuse instead.
    allow_reuse_address = sys.platform != 'win32'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=str(ROOT / 'config.json'))
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    if (ROOT / 'filters.json').exists():
        FILTERS.update(validate_filters(json.loads((ROOT / 'filters.json').read_text(encoding='utf-8-sig'))))
    config['fall'] = load_fall_config(config.get('fall', {}))
    PUBLIC_INFO.update(fall=config['fall'],motion_filter_enabled=config.get('motion_filter', {}).get('enabled', True), device='IWR6843AOP', vital_signs_available=False, posture_classification_available=True, postures=list(POSTURES), room_limit=config.get('room_limit'))
    if config['cli_port'] == config['data_port']:
        raise ValueError('CLI and DATA ports must be different')
    cfg = Path(config['cfg_path'])
    config['cfg_path'] = str(cfg if cfg.is_absolute() else ROOT / cfg)
    if not Path(config['cfg_path']).is_file():
        raise FileNotFoundError('Check cfg_path: ' + config['cfg_path'])
    common = Path(config['toolbox_root']) / 'tools/visualizers/Applications_Visualizer/common' if config.get('toolbox_root') else None
    if common is not None and (common / 'parseFrame.py').is_file():
        sys.path.insert(0, str(common))
        from parseFrame import parseStandardFrame
    else:
        # No TI Toolbox installed: use the bundled parser.
        from ti_parser import parse_frame as parseStandardFrame
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(), RotatingFileHandler(ROOT / 'service.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')])
    with database():
        pass
    # Bind the web port first: if another copy is already running, stop here before touching the COM ports.
    server = Server(('127.0.0.1', config['http_port']), Handler)
    worker = threading.Thread(target=radar_worker, args=(config, parseStandardFrame), daemon=True)
    worker.start()
    print('Open http://127.0.0.1:' + str(config['http_port']), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        server.server_close()
        worker.join(timeout=6)


if __name__ == '__main__':
    main()
