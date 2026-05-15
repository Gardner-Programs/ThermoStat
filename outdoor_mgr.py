"""
outdoor_mgr.py — Fetches outdoor temperature from Open-Meteo (free, no API key).
Caches the result for 30 minutes to avoid hammering the API.
Lat/lon are read from secrets.py (LAT, LON).
"""

import time

try:
    import urequests as requests
except ImportError:
    import requests

try:
    from secrets import LAT as _LAT, LON as _LON
except ImportError:
    _LAT = 0.0
    _LON = 0.0

_INTERVAL_MS = 30 * 60 * 1000   # refresh every 30 minutes
_URL         = (
    'http://api.open-meteo.com/v1/forecast'
    '?latitude={}&longitude={}'
    '&current=temperature_2m&temperature_unit=fahrenheit'
    '&wind_speed_unit=mph'
).format(_LAT, _LON)

_cached_temp = None
_last_fetch  = None
_wdt         = None


def set_wdt(wdt):
    """Call once after WDT is created in main.py so fetch can feed it."""
    global _wdt
    _wdt = wdt


def get_temp():
    """Return cached outdoor temp (°F). Refreshes every 30 min. Returns None on failure.
    Only call from the thermostat thread — triggers a blocking fetch when cache expires."""
    global _last_fetch
    now = time.ticks_ms()
    if _last_fetch is None or time.ticks_diff(now, _last_fetch) >= _INTERVAL_MS:
        _fetch()
    return _cached_temp


def get_cached():
    """Return cached temp without triggering a fetch. Safe to call from the web thread."""
    return _cached_temp


def _fetch():
    global _cached_temp, _last_fetch
    _last_fetch = time.ticks_ms()   # update before attempt to avoid tight retry loops
    if _wdt:
        _wdt.feed()   # fresh 8s window — DNS + connect can block with no socket timeout
    try:
        r = requests.get(_URL, timeout=6)
        status = r.status_code
        if status == 200:
            _cached_temp = r.json()['current']['temperature_2m']
            r.close()
            print('[Outdoor] {:.1f}F'.format(_cached_temp))
        else:
            r.close()
            print('[Outdoor] HTTP {} — retrying next cycle'.format(status))
            _last_fetch = None   # allow immediate retry on next call
    except Exception as e:
        print('[Outdoor] fetch error:', e)
    if _wdt:
        _wdt.feed()   # done blocking — reset so rest of loop has full 8s
