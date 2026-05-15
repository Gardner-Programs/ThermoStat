import time
import ntptime

_synced = False


def _nth_sunday(year, month, n):
    """Return UTC timestamp of midnight on the Nth Sunday of the given month."""
    t0  = time.mktime((year, month, 1, 0, 0, 0, 0, 0))
    dow = time.localtime(t0)[6]          # 0=Mon … 6=Sun
    day = 1 + (6 - dow) % 7 + (n - 1) * 7
    return time.mktime((year, month, day, 0, 0, 0, 0, 0))


def _is_dst(utc_ts):
    """US Eastern DST: 2nd Sunday March 07:00 UTC → 1st Sunday November 06:00 UTC."""
    year   = time.localtime(utc_ts)[0]
    spring = _nth_sunday(year, 3,  2) + 7 * 3600   # 2:00 AM EST = 07:00 UTC
    fall   = _nth_sunday(year, 11, 1) + 6 * 3600   # 2:00 AM EDT = 06:00 UTC
    return spring <= utc_ts < fall


def sync_time():
    global _synced
    try:
        ntptime.settime()
        _synced = True
        print("NTP sync complete.")
    except:
        print("NTP sync failed.")


def is_synced():
    return _synced


def get_time_tuple():
    utc    = time.time()
    offset = -4 * 3600 if _is_dst(utc) else -5 * 3600
    return time.localtime(utc + offset)


def get_local_time_string():
    t = get_time_tuple()
    return "{:02d}:{:02d}:{:02d}".format(t[3], t[4], t[5])
