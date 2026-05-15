"""
learn_mgr.py — Adaptive pre-conditioning learner.

Each day at 21:00, analyses today's history.csv to find the heating episode
that occurred during the pre-conditioning window before the user arrived home.
It measures the actual heating rate (°F/min), stores it alongside previous
episodes, and recalculates the optimal pre-conditioning start time so the home
reaches target temp right as the user walks in.

Requires at least MIN_EPISODES days of data before it will touch the schedule.
"""

import json
import os
import sched_mgr
import time_mgr
import outdoor_mgr

_LEARN_FILE   = 'learning.json'
_MAX_EPISODES = 7      # rolling window of days to average over
_MIN_EPISODES = 3      # minimum before auto-adjusting schedule
_LOOKBACK_H   = 3      # hours before window-end to search for episode
_BUFFER       = 1.15   # add 15% time buffer on top of calculated need
_MIN_PRE      = 30     # clamp floor  (minutes)
_MAX_PRE      = 120    # clamp ceiling (minutes)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load():
    try:
        if _LEARN_FILE in os.listdir():
            with open(_LEARN_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return {'episodes': [], 'avg_rate': None, 'pre_con': None}


def _save(data):
    try:
        with open(_LEARN_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        print('[Learn] save error:', e)


# ---------------------------------------------------------------------------
# History parsing — rolling buffer, reads line-by-line to save RAM
# ---------------------------------------------------------------------------

def _read_recent(max_entries):
    """
    Read history.csv keeping only the last max_entries lines.
    Returns list of (day_of_month, mins_since_midnight, temp_f, state_char).
    """
    buf = []
    try:
        with open('history.csv', 'r') as f:
            for line in f:
                line = line.strip()
                if len(line) < 12:
                    continue
                p = line.split(',')
                if len(p) < 4:
                    continue
                try:
                    s = p[0]
                    entry = (
                        int(s[4:6]),                        # day of month
                        int(s[6:8]) * 60 + int(s[8:10]),  # mins since midnight
                        int(p[1]) / 10.0,                  # temp °F
                        p[3].strip()[0] if p[3].strip() else 'I'  # state char
                    )
                    buf.append(entry)
                    if len(buf) > max_entries:
                        buf.pop(0)
                except:
                    pass
    except Exception as e:
        print('[Learn] read error:', e)
    return buf


# ---------------------------------------------------------------------------
# Episode detection
# ---------------------------------------------------------------------------

def _find_rate(buf, window_end_mins, today_day):
    """
    Find the heating rate for a pre-conditioning episode that ended at
    window_end_mins on today_day.

    Looks for the pattern: state 'A' (away) → state 'H' (heating) in the
    LOOKBACK_H hours before the window end.  Measures °F rise over the heating
    period up to the first 'I' (idle = target reached) or end of window.

    Returns °F/min, or None if no valid episode found.
    """
    cutoff = window_end_mins - _LOOKBACK_H * 60

    relevant = [
        (m, t, s) for (d, m, t, s) in buf
        if d == today_day and cutoff <= m <= window_end_mins
    ]

    if len(relevant) < 4:
        return None

    # Find first heating entry (pre-con start)
    heat_start = None
    for i, (m, t, s) in enumerate(relevant):
        if s == 'H':
            heat_start = i
            break

    if heat_start is None:
        return None

    # Collect consecutive heating entries; stop at first idle (target reached)
    heat = []
    for (m, t, s) in relevant[heat_start:]:
        if s == 'I':
            break   # target was reached — ideal endpoint
        if s == 'H':
            heat.append((m, t))

    if len(heat) < 3:
        return None

    duration = heat[-1][0] - heat[0][0]   # minutes
    rise     = heat[-1][1] - heat[0][1]   # °F

    if duration < 5 or rise <= 0:
        return None

    rate = rise / duration
    print('[Learn] Episode: {:.1f}F -> {:.1f}F over {}min = {:.4f}F/min'.format(
        heat[0][1], heat[-1][1], duration, rate))
    return rate


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run():
    """
    Analyse today's history, update episode log, and adjust schedule pre-con.
    Safe to call daily regardless of episode count — does nothing until
    MIN_EPISODES days of data are available.
    """
    print('[Learn] Running daily analysis...')

    tt = time_mgr.get_time_tuple()
    if not tt:
        print('[Learn] No time available')
        return

    day_map = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    today   = day_map[tt[6]]
    today_d = tt[2]

    sched     = sched_mgr.load_schedule()
    home_temp = float(sched.get('home_temp', 72.0))
    windows   = sched.get('windows', [])

    # Rolling buffer covers last LOOKBACK_H hours + a little extra
    max_buf = (_LOOKBACK_H + 1) * 12   # 12 entries/hr at 5-min intervals
    buf = _read_recent(max_buf)
    if not buf:
        print('[Learn] No history data yet')
        return

    data    = load()
    changed = False

    for i, w in enumerate(windows):
        if w.get('type') != 'away':
            continue
        if today not in w.get('days', []):
            continue

        end_str  = w.get('end', '17:00')
        try:
            eh, em   = end_str.split(':')
            end_mins = int(eh) * 60 + int(em)
        except:
            continue

        hl = float(w.get('heat_limit', 62))

        rate = _find_rate(buf, end_mins, today_d)
        if rate is None:
            print('[Learn] No valid episode found for window ending {}'.format(end_str))
            continue

        # Store episode (date string + rate + outdoor temp for future bucketing)
        out = outdoor_mgr.get_temp()
        data['episodes'].append({
            'd':   '{:02d}{:02d}{:02d}'.format(tt[0] % 100, tt[1], tt[2]),
            'r':   rate,
            'out': out
        })
        if len(data['episodes']) > _MAX_EPISODES:
            data['episodes'] = data['episodes'][-_MAX_EPISODES:]

        n = len(data['episodes'])
        if n < _MIN_EPISODES:
            print('[Learn] Collecting data — {}/{} episodes'.format(n, _MIN_EPISODES))
            _save(data)
            continue

        # Average heating rate across all stored episodes
        avg_rate = sum(e['r'] for e in data['episodes']) / n

        if avg_rate <= 0:
            print('[Learn] avg_rate={:.4f} — skipping (bad episode data)'.format(avg_rate))
            continue

        # Time needed = temp delta / rate, plus safety buffer
        needed  = (home_temp - hl) / avg_rate
        pre_con = int(min(max(needed * _BUFFER, _MIN_PRE), _MAX_PRE))

        data['avg_rate'] = avg_rate
        data['pre_con']  = pre_con

        print('[Learn] avg={:.4f}F/min  needed={:.0f}min  pre={}min  ({} eps)'.format(
            avg_rate, needed, pre_con, n))

        # Only update schedule if change is meaningful (>= 5 min)
        old_pre = int(w.get('pre', 0))
        if abs(pre_con - old_pre) >= 5:
            windows[i]['pre'] = pre_con
            sched['windows']  = windows
            sched_mgr.save_schedule(sched)
            print('[Learn] Pre-con updated: {}min -> {}min'.format(old_pre, pre_con))
            changed = True

    _save(data)
    if not changed:
        print('[Learn] No schedule changes needed')
