import json

FILENAME = "schedule.json"
_cache   = None   # parsed schedule kept in RAM; only reloaded after save or first boot

DEFAULT_SCHEDULE = {
    "home_temp": 72.0,
    "windows": [
        {
            "type": "away",
            "start": "07:15", "end": "17:00",
            "days": ["mon","tue","wed","thu","fri"],
            "heat_limit": 58, "cool_limit": 84, "pre": 30
        }
    ]
}


def load_schedule():
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(FILENAME, "r") as f:
            data = json.load(f)
        if isinstance(data, dict) and 'home_temp' in data:
            # Migrate old away_windows format
            if 'away_windows' in data and 'windows' not in data:
                data['windows'] = []
                for w in data['away_windows']:
                    w['type'] = 'away'
                    data['windows'].append(w)
                del data['away_windows']
                save_schedule(data)
                print("[Sched] Migrated away_windows -> windows")
            _cache = data
            return _cache
        print("[Sched] Old format detected, resetting to default.")
    except OSError:
        pass
    except Exception as e:
        print("Error loading schedule: {}".format(e))
    save_schedule(DEFAULT_SCHEDULE)
    return DEFAULT_SCHEDULE


def save_schedule(data):
    global _cache
    _cache = data
    try:
        with open(FILENAME, "w") as f:
            json.dump(data, f)
        print("Schedule saved.")
    except Exception as e:
        print("Error saving schedule: {}".format(e))


def _time_to_mins(t_str):
    try:
        h, m = t_str.split(':')
        return int(h) * 60 + int(m)
    except:
        return 0


def get_scheduled_state(current_day, current_time_str):
    """
    Returns {temp, mode, away, heat_limit, cool_limit} for the current moment.

    Window types:
      "away" — away mode, only protect against extremes.
               Supports pre-conditioning: switches to home target X mins before end.
      "home" — actively target a specific temp (e.g. warm up before work).
    If no window is active, returns the default home_temp.
    """
    sched        = load_schedule()
    home_temp    = float(sched.get('home_temp', 72.0))
    day_key      = current_day.lower()[:3]
    current_mins = _time_to_mins(current_time_str)

    for w in sched.get('windows', []):
        if day_key not in w.get('days', []):
            continue

        start_mins = _time_to_mins(w.get('start', '00:00'))
        end_mins   = _time_to_mins(w.get('end',   '23:59'))

        if end_mins > start_mins:
            in_window = start_mins <= current_mins < end_mins
        else:   # crosses midnight
            in_window = current_mins >= start_mins or current_mins < end_mins

        if not in_window:
            continue

        win_type = w.get('type', 'away')

        if win_type == 'home':
            target = float(w.get('temp', home_temp))
            return {"temp": target, "mode": "auto", "away": False,
                    "heat_limit": 58, "cool_limit": 84}

        # away window
        heat_limit = w.get('heat_limit', 58)
        cool_limit = w.get('cool_limit', 84)
        pre_mins   = int(w.get('pre', 0))

        if pre_mins > 0:
            if end_mins > start_mins:
                mins_until_end = end_mins - current_mins
            else:
                mins_until_end = ((1440 - current_mins) + end_mins
                                  if current_mins >= start_mins
                                  else end_mins - current_mins)

            if mins_until_end <= pre_mins:
                print("[Sched] Pre-conditioning {}m before return".format(mins_until_end))
                return {"temp": home_temp, "mode": "auto", "away": False,
                        "heat_limit": heat_limit, "cool_limit": cool_limit}

        return {"temp": home_temp, "mode": "auto", "away": True,
                "heat_limit": heat_limit, "cool_limit": cool_limit}

    # No window active — maintain home temp
    return {"temp": home_temp, "mode": "auto", "away": False,
            "heat_limit": 58, "cool_limit": 84}
