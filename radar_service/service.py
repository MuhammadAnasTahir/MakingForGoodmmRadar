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
)
ALERT_KINDS = ('fall', 'prolonged_low')
MIN_REFERENCE_HISTORY_S = 1.0   # need this much history before trusting the reference height
SETTLE_DROP_M = 0.08            # still descending if height fell more than this over the last ~0.3 s
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
            if self._still_descending(tr, now) and now - tr.t_cross < c['confirm_seconds'] + SETTLE_MAX_WAIT_S:
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
                # Staged descent (e.g. kneel, then lie down): judge again once the drop is big.
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

    def _still_descending(self, tr, now):
        earlier = [v for ts, v in tr.hist if now - 0.5 <= ts <= now - 0.3]
        latest = [v for ts, v in tr.hist if ts >= now - 0.2]
        return bool(earlier and latest) and _median(earlier) - _median(latest) > SETTLE_DROP_M

    def _features(self, tr, now):
        c = self.config
        pts = list(tr.hist)
        recent = [v for ts, v in pts if ts >= now - min(c['confirm_seconds'], LOW_WINDOW_S)]
        low = _median(recent)
        reference = tr.reference
        drop = reference - low
        lvl10, lvl90 = reference - 0.1 * drop, reference - 0.9 * drop
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
        detector = FallDetector(config['fall'])
        classifier = MotionClassifier()
        try:
            with LOCK:
                STATE.update(status='configuring', tracks=[], points=[], last_frame_at=None, error=None)
            with serial.Serial(config['cli_port'], 115200, timeout=0.15, write_timeout=2) as cli:
                with serial.Serial(config['data_port'], config['data_baud'], timeout=0.2) as data:
                    data.reset_input_buffer()
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
                        tracks = filter_tracks(tracks, settings)
                        region_count = len(tracks)
                        now = time.monotonic()
                        tracks = classifier.update(tracks, now) if config.get('motion_filter', {}).get('enabled', True) else tracks
                        detector.update(tracks, now)
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
        # Serve only web/. Never expose config, logs, database, or Python source.
        path = unquote(urlsplit(self.path).path)
        relative = 'index.html' if path == '/' else path.lstrip('/')
        webroot = (ROOT / 'web').resolve()
        target = (webroot / relative).resolve()
        if webroot not in target.parents or not target.is_file():
            return self.send_error(404)
        if target.suffix.lower() not in {'.html','.js','.css','.png','.jpg','.jpeg','.svg','.webp','.ico'}:
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
    for key in ('min_drop_m', 'max_low_height_m', 'fall_max_duration_s', 'fall_min_speed_mps', 'floor_alert_s',
                'reference_seconds', 'speed_span_s', 'confirm_seconds', 'cooldown_seconds'):
        value = merged[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('fall.' + key + ' must be a positive number')
    return merged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=str(ROOT / 'config.json'))
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    if (ROOT / 'filters.json').exists():
        FILTERS.update(validate_filters(json.loads((ROOT / 'filters.json').read_text(encoding='utf-8-sig'))))
    config['fall'] = load_fall_config(config.get('fall', {}))
    PUBLIC_INFO.update(fall=config['fall'],motion_filter_enabled=config.get('motion_filter', {}).get('enabled', True), device='IWR6843AOP', vital_signs_available=False, posture_classification_available=False)
    if config['cli_port'] == config['data_port']:
        raise ValueError('CLI and DATA ports must be different')
    common = Path(config['toolbox_root']) / 'tools/visualizers/Applications_Visualizer/common'
    if not (common / 'parseFrame.py').is_file():
        raise FileNotFoundError('Check toolbox_root: ' + str(common))
    if not Path(config['cfg_path']).is_file():
        raise FileNotFoundError('Check cfg_path: ' + config['cfg_path'])
    sys.path.insert(0, str(common))
    from parseFrame import parseStandardFrame
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(), RotatingFileHandler(ROOT / 'service.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')])
    with database():
        pass
    worker = threading.Thread(target=radar_worker, args=(config, parseStandardFrame), daemon=True)
    worker.start()
    server = ThreadingHTTPServer(('127.0.0.1', config['http_port']), Handler)
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
