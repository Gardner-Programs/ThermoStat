import time
import os
import time_mgr

_LOG_FILE     = 'history.csv'
_MAX_LINES    = 1000          # ~3.5 days @ 5-min intervals (keeps file ~26KB)
_LOG_INTERVAL = 5 * 60 * 1000 # ms
_last_log_ms  = None


def maybe_log(temp_f, humidity, state, outdoor_temp=None):
    global _last_log_ms
    now = time.ticks_ms()
    if _last_log_ms is not None and time.ticks_diff(now, _last_log_ms) < _LOG_INTERVAL:
        return
    _last_log_ms = now
    _append(temp_f, humidity, state, outdoor_temp)


def _append(temp_f, humidity, state, outdoor_temp=None):
    try:
        tt = time_mgr.get_time_tuple()
        if not tt:
            return
        if tt[0] < 2024:   # NTP hasn't synced yet — skip to avoid bad timestamps
            return
        ts   = '{:02d}{:02d}{:02d}{:02d}{:02d}'.format(
                    tt[0] % 100, tt[1], tt[2], tt[3], tt[4])
        sc   = (state[0].upper() if state else 'I')
        if outdoor_temp is not None:
            line = '{},{},{},{},{}\n'.format(
                        ts,
                        int(round(temp_f * 10)),
                        int(round(humidity * 10)),
                        sc,
                        int(round(outdoor_temp * 10)))
        else:
            line = '{},{},{},{}\n'.format(
                        ts,
                        int(round(temp_f * 10)),
                        int(round(humidity * 10)),
                        sc)
        with open(_LOG_FILE, 'a') as f:
            f.write(line)
        # Trim when file exceeds max size — threshold must be well above
        # MAX_LINES * max_bytes_per_line to avoid trim-every-entry loop
        try:
            if os.stat(_LOG_FILE)[6] > 55000:
                _trim()
        except Exception:
            pass
    except Exception as e:
        print('[DataMgr] log error:', e)


def _trim():
    """Remove oldest entries keeping _MAX_LINES, reading line-by-line to save RAM."""
    try:
        count = 0
        with open(_LOG_FILE, 'r') as f:
            for _ in f:
                count += 1
        skip = count - _MAX_LINES
        if skip <= 0:
            return
        tmp = _LOG_FILE + '.t'
        with open(_LOG_FILE, 'r') as r:
            with open(tmp, 'w') as w:
                for i, line in enumerate(r):
                    if i >= skip:
                        w.write(line)
        os.remove(_LOG_FILE)
        os.rename(tmp, _LOG_FILE)
        print('[DataMgr] Trimmed to {} lines'.format(_MAX_LINES))
    except Exception as e:
        print('[DataMgr] trim error:', e)
