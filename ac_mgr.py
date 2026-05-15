import time
import json
try:
    import outdoor_mgr as _outdoor
except:
    _outdoor = None

ac_power = False
ac_mode  = "cool"   # cool | heat | fan | dry
ac_fan   = "auto"   # auto | low | mid | high | turbo

_AC_STATE_FILE = "ac_state.json"

_boot_ir_temp = None   # last IR setpoint — restored on boot for resend

def _load_ac_state():
    global ac_power, ac_mode, ac_fan, _boot_ir_temp
    try:
        with open(_AC_STATE_FILE, "r") as f:
            d = json.load(f)
        ac_power      = d.get("power", False)
        ac_mode       = d.get("mode", "cool")
        ac_fan        = d.get("fan",  "auto")
        _boot_ir_temp = d.get("ir_temp", None)
        print("[AC] Restored state: power={} mode={} fan={}".format(ac_power, ac_mode, ac_fan))
    except OSError:
        pass
    except Exception as e:
        print("[AC] State load failed:", e)

def _save_ac_state():
    try:
        with open(_AC_STATE_FILE, "w") as f:
            json.dump({"power": ac_power, "mode": ac_mode, "fan": ac_fan,
                       "ir_temp": _boot_ir_temp}, f)
    except Exception as e:
        print("[AC] State save failed:", e)


def boot_resend():
    """Resend saved IR state on boot so Daikin matches ESP32 state after a reboot."""
    global ac_power, _last_sent_mode
    if not ac_power or _boot_ir_temp is None:
        return
    # Don't resend cool when it's cold outside — cool suppress would fire immediately
    # after the resend, leaving the Daikin physically on but software tracking it as off.
    if ac_mode == 'cool':
        out_temp = _outdoor.get_temp() if _outdoor else None
        if out_temp is not None and out_temp < _COOL_HARD_SUPPRESS_F:
            print("[AC] Boot resend skipped — cool mode but outdoor {:.0f}F < {:.0f}F, sending off".format(
                out_temp, _COOL_HARD_SUPPRESS_F))
            ac_power = False
            _last_sent_mode = None   # force smart logic to re-evaluate mode fresh
            _save_ac_state()
            try:
                import ir_daikin
                ir_daikin.send(power=False, mode=ac_mode, temp_f=_boot_ir_temp, fan=ac_fan)
            except Exception as e:
                print("[AC] Boot resend off error:", e)
            return
    print("[AC] Boot resend: power={} mode={} fan={} temp={:.1f}F".format(
        ac_power, ac_mode, ac_fan, _boot_ir_temp))
    _last_sent_mode = ac_mode   # initialise so dead-band fallback picks correct mode
    try:
        import ir_daikin
        ir_daikin.send(power=True, mode=ac_mode, temp_f=_boot_ir_temp, fan=ac_fan)
    except Exception as e:
        print("[AC] Boot resend error:", e)

_load_ac_state()

# ---------------------------------------------------------------------------
# Minimum off-time lockout — prevents short-cycling after target is reached
# ---------------------------------------------------------------------------
_MIN_OFF_MS      = 20 * 60 * 1000   # 20 min
_off_until_ticks = None              # None = no lockout pending

# ---------------------------------------------------------------------------
# Manual override — blocks smart/schedule logic for a fixed period
# ---------------------------------------------------------------------------
_OVERRIDE_MS          = 60 * 60 * 1000   # 1 hour default
_override_until_ticks = None             # None = smart mode in control

# ---------------------------------------------------------------------------
# Internal fan-tier tracking + heartbeat resend
# ---------------------------------------------------------------------------
_last_fan        = None   # last tier actually sent
_last_fan_ticks  = None   # ticks_ms() when last changed
_MIN_FAN_INTERVAL = 20 * 60 * 1000  # ms — rate-limit fan tier changes (20 min)

_last_ir_ticks   = None   # ticks_ms() of last IR send (any command)
_last_ac_off_ticks = None  # ticks_ms() when AC was last commanded off
_HEARTBEAT_MS      = 20 * 60 * 1000  # resend every 20 min while AC should be on

_last_sent_mode  = None   # mode last sent via IR (separate from ac_mode which
                           # the schedule can overwrite with 'auto')

# ---------------------------------------------------------------------------
# Mode-guard thresholds
# ---------------------------------------------------------------------------
_DEAD_BAND        = 1.0              # °F either side of target — off instead of flipping
_HEAT_SEASON_OUT  = 55.0            # outdoor below this → suppress auto-cool
_COOL_SEASON_OUT  = 75.0            # outdoor above this → suppress auto-heat
_MODE_OVERRIDE_MS = 45 * 60 * 1000  # allow opposite mode after this long out of range
_mode_guard_start = None            # ticks_ms when room first entered season-conflict zone
_COOL_HARD_SUPPRESS_F = 70.0        # outdoor below this → never run AC cooling (open a window)

# Persistence boost — if heating with small delta for a long time, slide IR setpoint up
# so the Daikin works harder against heat loss rather than just idling at target temp.
#   stuck  0–30 min : no extra boost (normal operation)
#   stuck 30–60 min : +0 to +2°F sliding
#   stuck 60–90 min : +2 to +4°F sliding
#   stuck  90+ min  : +4°F (cap — don't overshoot too far above target)
_PERSIST_DELTA_THRESHOLD = 3.0       # only counts as "stuck" when delta < 3°F
_PERSIST_START_MS        = 30 * 60 * 1000  # start adding boost after 30 min stuck
_PERSIST_MAX_BOOST       = 4.0       # cap on persistence bonus (°F)
_heat_slow_since         = None      # ticks_ms when we first detected slow-progress heating

# Daikin runaway check — detects Daikin running when commanded off
# Triggers when room rises above a threshold in the 15-min window while ac_power=False
# and outdoor is at least _RUNAWAY_MIN_GAP_F below indoor.
# Threshold scales DOWN as the gap grows: when it's very cold outside the cold pulls
# heat out faster than neighbor bleed could push it in, so any rise is suspicious.
# On mild days neighbor bleed through shared walls is more plausible, so we need a
# larger rise before firing.
#   gap  15°F → threshold 2.5°F  (mild, neighbor bleed plausible)
#   gap  25°F → threshold 2.0°F
#   gap  35°F → threshold 1.5°F
#   gap  45°F → threshold 1.0°F  (floor — freezing, any rise is suspicious)
_RUNAWAY_MIN_GAP_F    = 15.0            # outdoor must be this far below indoor before check runs
_RUNAWAY_BASE_F       = 2.5             # threshold at minimum gap (mild conditions)
_RUNAWAY_SCALE        = 0.05            # subtract this many °F per °F of gap above minimum
_RUNAWAY_MIN_F        = 1.0             # floor — always require at least this much rise
_runaway_fired_ms     = None            # ticks of last resend, for cooldown


# ---------------------------------------------------------------------------
# Fan tier table
# ---------------------------------------------------------------------------
def _fan_for_delta(delta, stuck_minutes=0):
    """delta = absolute degrees F between current and target (always positive).
    stuck_minutes = how long delta has been below _PERSIST_DELTA_THRESHOLD.
    Minimum tier is 'mid'. Escalates to high/turbo based on delta, and also
    bumps up one tier after 30/60 min stuck so the fan keeps pace with the
    persistence IR boost."""
    if delta >= 5.0:
        tier = 'turbo'
    elif delta >= 2.0:
        tier = 'high'
    else:
        tier = 'mid'
    # Persistence escalation — bump up a tier after extended stuck periods
    if stuck_minutes >= 60 and tier != 'turbo':
        tier = 'turbo' if tier == 'high' else 'high'
    elif stuck_minutes >= 30 and tier == 'mid':
        tier = 'high'
    return tier


# ---------------------------------------------------------------------------
# Heat boost — raise the IR setpoint so Daikin works harder when far behind.
# The thermostat still shuts off at actual target_temp; this only affects
# what temperature the Daikin unit itself is told to reach.
# ---------------------------------------------------------------------------
def _heat_boost(eff_target, delta, out_temp=None, stuck_minutes=0):
    """Return a boosted IR setpoint for heating mode.
    delta boost: scales with how far below target the room is.
    persistence boost: if delta has been small for >30 min, slides IR setpoint up
      so the Daikin works harder against heat loss rather than idling at target temp.
    cold boost: extra 2°F when outdoor is below freezing."""
    if delta >= 6.0:
        boost = 8.0
    elif delta >= 4.0:
        boost = 6.0
    elif delta >= 2.0:
        boost = 4.0
    elif delta >= 1.0:
        boost = 2.0
    else:
        boost = 0.0
    # Persistence boost — slides up after 30 min of slow progress.
    # Applies even when delta < 1°F: room near target but Daikin at low output can't
    # overcome heat loss at cold outdoor temps. Higher IR setpoint = Daikin runs harder,
    # reaches target faster, shuts off cleanly — better for energy than idling all night.
    if stuck_minutes > 30:
        persist = min(_PERSIST_MAX_BOOST, (stuck_minutes - 30) / 30.0 * 2.0)
        boost += persist
    # Extra push when it's below freezing outside
    if out_temp is not None and out_temp < 32.0:
        boost += 2.0
    return min(eff_target + boost, 86.0)  # cap at 86°F (Daikin max)


# ---------------------------------------------------------------------------
# Manual override helpers
# ---------------------------------------------------------------------------
def is_override_active():
    if _override_until_ticks is None:
        return False
    return time.ticks_diff(_override_until_ticks, time.ticks_ms()) > 0


def set_manual_override(duration_ms=None):
    global _override_until_ticks
    _override_until_ticks = time.ticks_ms() + (duration_ms or _OVERRIDE_MS)
    print("[Override] Manual override active for {}m".format(
        (duration_ms or _OVERRIDE_MS) // 60000))


def clear_override():
    global _override_until_ticks, _off_until_ticks
    _override_until_ticks = None
    _off_until_ticks = None   # also clear lockout — user explicitly resumed control
    print("[Override] Cleared — resuming smart/schedule control")


# ---------------------------------------------------------------------------
# IR command sender — used by both smart logic and web UI manual commands
# ---------------------------------------------------------------------------
def set_ir_command(power, mode, fan, temp):
    global ac_power, ac_mode, ac_fan, _last_fan, _last_fan_ticks, _off_until_ticks, _last_sent_mode, _boot_ir_temp
    ac_power = power
    ac_mode  = mode
    ac_fan   = fan
    _last_sent_mode = mode
    if power:
        _boot_ir_temp    = temp
        _last_fan        = fan
        _last_fan_ticks  = time.ticks_ms()
        _off_until_ticks = None              # turning on clears lockout
    else:
        _last_fan        = None
        _last_fan_ticks  = None
        _off_until_ticks = time.ticks_ms() + _MIN_OFF_MS   # start lockout
        global _last_ac_off_ticks
        _last_ac_off_ticks = time.ticks_ms()
    global _last_ir_ticks
    _last_ir_ticks = time.ticks_ms()
    print("[IR] power={}  mode={}  fan={}  temp={:.1f}F".format(power, mode, fan, temp))
    _save_ac_state()
    try:
        import ir_daikin
        ir_daikin.send(power=power, mode=mode, temp_f=temp, fan=fan)
        if not power:
            # Off commands are critical — send a second time after a short gap
            import utime
            utime.sleep_ms(200)
            ir_daikin.send(power=False, mode=mode, temp_f=temp, fan=fan)
    except Exception as e:
        print("[IR] send error:", e)


# ---------------------------------------------------------------------------
# Smart thermostat brain — called from main loop every iteration
# ---------------------------------------------------------------------------
def check_and_update(current_temp, target_temp, away=False, heat_limit=58, cool_limit=84):
    """
    Smart control logic.

    away=True  : only run to protect against extreme temps (below heat_limit
                 or above cool_limit); shut off otherwise. Bypasses lockout
                 for emergency protection.
    away=False : maintain target_temp with automatic heat-vs-cool selection
                 (Daikin 'auto' mode is unreliable — we decide ourselves).

    Lockout  : after shutoff, waits MIN_OFF_MS before restarting.
    Override : manual web-UI commands pause smart adjustments for OVERRIDE_MS.
    """
    global _last_fan, _last_fan_ticks, _heat_slow_since
    now = time.ticks_ms()
    out_temp = _outdoor.get_temp() if _outdoor else None

    # ------------------------------------------------------------------ AWAY
    if away:
        if current_temp < heat_limit:
            eff_mode, eff_target = 'heat', float(heat_limit)
        elif current_temp > cool_limit:
            eff_mode, eff_target = 'cool', float(cool_limit)
        else:
            # Temp is in the comfortable range while away — ensure AC is off
            if ac_power:
                print("[Smart] Away — temp OK ({:.1f}F). Shutting off.".format(current_temp))
                set_ir_command(False, ac_mode, ac_fan, current_temp)
            return "AWAY"
        # Falls through to shared run-logic with eff_mode/eff_target set

    # --------------------------------------------------------------- NORMAL
    else:
        # Honour manual override — no automatic changes while it's active
        if is_override_active():
            if not ac_power:
                return "IDLE"
            if ac_mode in ('fan', 'dry'):
                return ac_mode.upper()
            return "HEATING" if (current_temp < target_temp) else "COOLING"

        # fan / dry — no temperature target, just run
        if ac_power and ac_mode in ('fan', 'dry'):
            return ac_mode.upper()

        # Own auto heat/cool decision (avoids broken Daikin 'auto' mode)
        global _mode_guard_start
        eff_target = target_temp
        now        = time.ticks_ms()

        # What does the room need right now?
        if current_temp < eff_target - _DEAD_BAND:
            needed = 'heat'
        elif current_temp > eff_target + _DEAD_BAND:
            needed = 'cool'
        else:
            # In dead band
            _mode_guard_start = None
            if not ac_power:
                return "IDLE"   # off and in range — stay off
            # Already running — don't force off here, let overshoot threshold handle it
            needed = _last_sent_mode if _last_sent_mode in ('heat', 'cool') else (
                'heat' if current_temp <= eff_target else 'cool')

        # Hard-suppress cooling when outdoor is cool enough to ventilate naturally.
        # Unlike the season_conflict timer this never times out — no AC if it's cool outside.
        # Exception: don't suppress if we're actively heating and the room just overshot —
        # the target-reached guard (delta <= -1.0) handles that cleanly without a restart cycle.
        heating_overshoot = (ac_power and _last_sent_mode == 'heat')
        if needed == 'cool' and out_temp is not None and out_temp < _COOL_HARD_SUPPRESS_F and not heating_overshoot:
            if ac_power:
                print("[Smart] Cool suppressed — outdoor {:.0f}F < {:.0f}F. Off.".format(
                    out_temp, _COOL_HARD_SUPPRESS_F))
                set_ir_command(False, ac_mode, ac_fan, eff_target)
            _mode_guard_start = None
            return "IDLE"

        # Is this season-appropriate?
        season_conflict = (
            out_temp is not None and (
                (needed == 'cool' and out_temp < current_temp) or   # outdoor cooler — room will cool naturally
                (needed == 'heat' and out_temp > current_temp)      # outdoor warmer — room will heat naturally
            )
        )

        if not season_conflict:
            _mode_guard_start = None
            eff_mode = needed
        else:
            # Track how long room has been stuck in the conflict zone
            if _mode_guard_start is None:
                _mode_guard_start = now
            elapsed_min = time.ticks_diff(now, _mode_guard_start) // 60000

            if time.ticks_diff(now, _mode_guard_start) < _MODE_OVERRIDE_MS:
                # Suppress — shut off and wait it out
                if ac_power:
                    print("[Smart] Season guard: need {} but out={:.0f}F ({}min/45min). Off.".format(
                        needed, out_temp, elapsed_min))
                    set_ir_command(False, ac_mode, ac_fan, eff_target)
                _heat_slow_since = None
                return "IDLE"
            else:
                # Held too long — allow the opposite mode as emergency override
                print("[Smart] Season override after {}min. Allowing {}.".format(
                    elapsed_min, needed))
                eff_mode = needed

    # ---------------------------------------------------- SHARED RUN LOGIC
    # Delta: positive = still work to do
    if eff_mode == 'heat':
        delta = eff_target - current_temp
    else:
        delta = current_temp - eff_target

    # Target reached — turn off (allow 1°F overshoot before cutting off)
    if delta <= -1.0:
        if ac_power:
            print("[Smart] Target reached {:.1f}F @ {:.1f}F set. Power off.".format(
                current_temp, eff_target))
            set_ir_command(False, eff_mode, ac_fan, eff_target)
        _heat_slow_since = None
        return "AWAY" if away else "IDLE"

    # Track slow-progress heating (small delta, Daikin running but barely gaining)
    if eff_mode == 'heat' and ac_power:
        if delta < _PERSIST_DELTA_THRESHOLD:
            if _heat_slow_since is None:
                _heat_slow_since = now
        else:
            _heat_slow_since = None   # making good progress — reset
    else:
        _heat_slow_since = None
    stuck_minutes = (time.ticks_diff(now, _heat_slow_since) // 60000
                     if _heat_slow_since is not None else 0)

    # Need to run — check lockout before (re)starting (bypassed in away emergency)
    if not ac_power:
        if not away and _off_until_ticks is not None and time.ticks_diff(_off_until_ticks, now) > 0:
            return "LOCKOUT"
        needed_fan = _fan_for_delta(delta, stuck_minutes)
        ir_target  = _heat_boost(eff_target, delta, out_temp, stuck_minutes) if eff_mode == 'heat' else eff_target
        print("[Smart] Start {} to {:.1f}F (IR={:.1f}F) fan={}".format(
            eff_mode, eff_target, ir_target, needed_fan))
        set_ir_command(True, eff_mode, needed_fan, ir_target)
        return "HEATING" if eff_mode == 'heat' else "COOLING"

    # Already running — adjust fan tier or mode if needed, or heartbeat resend
    needed_fan   = _fan_for_delta(delta, stuck_minutes)
    ir_target    = _heat_boost(eff_target, delta, out_temp, stuck_minutes) if eff_mode == 'heat' else eff_target
    first_run    = (_last_fan is None)
    tier_changed = (needed_fan != _last_fan)
    mode_changed = (_last_sent_mode is not None and eff_mode != _last_sent_mode)
    cooldown_ok  = first_run or (time.ticks_diff(now, _last_fan_ticks) >= _MIN_FAN_INTERVAL)
    heartbeat    = (_last_ir_ticks is None or
                    time.ticks_diff(now, _last_ir_ticks) >= _HEARTBEAT_MS)

    if heartbeat:
        stuck_note = ' stuck={}m'.format(stuck_minutes) if stuck_minutes > 30 else ''
        print("[Smart] Heartbeat resend — mode={} fan={} temp={:.1f}F (IR={:.1f}F{})".format(
            eff_mode, needed_fan, eff_target, ir_target, stuck_note))
        set_ir_command(True, eff_mode, needed_fan, ir_target)
    elif (tier_changed or mode_changed) and cooldown_ok:
        print("[Smart] Delta={:.1f}F → mode {} fan {} -> {} {}".format(
            delta, eff_mode, _last_fan, needed_fan, '(mode flip)' if mode_changed else ''))
        set_ir_command(True, eff_mode, needed_fan, ir_target)

    return "HEATING" if eff_mode == 'heat' else "COOLING"


# ---------------------------------------------------------------------------
# Daikin runaway detection — resend off if room rises while AC commanded off
# ---------------------------------------------------------------------------
_SANITY_WINDOW_MS  = 15 * 60 * 1000
_SANITY_COOLDOWN   = 30 * 60 * 1000
_temp_history      = []
_runaway_fired_ms  = None

def check_sanity(current_temp, out_temp):
    global _temp_history, _runaway_fired_ms
    now = time.ticks_ms()
    _temp_history.append((now, current_temp))
    cutoff = now - _SANITY_WINDOW_MS
    _temp_history = [(t, v) for (t, v) in _temp_history if t >= cutoff]

    if len(_temp_history) < 3:
        return

    oldest_temp = _temp_history[0][1]
    rise = current_temp - oldest_temp

    # Daikin runaway: room rising with Daikin commanded off.
    # Threshold scales with outdoor-indoor gap — colder outside = lower threshold.
    # Skip for 20 min after AC last turned off (normal post-run equilibration).
    _RUNAWAY_POST_OFF_MS = 20 * 60 * 1000
    recently_off = (_last_ac_off_ticks is not None and
                    time.ticks_diff(now, _last_ac_off_ticks) < _RUNAWAY_POST_OFF_MS)
    if not ac_power and out_temp is not None and not recently_off:
        gap = current_temp - out_temp
        if gap >= _RUNAWAY_MIN_GAP_F:
            threshold = max(_RUNAWAY_MIN_F,
                            _RUNAWAY_BASE_F - (gap - _RUNAWAY_MIN_GAP_F) * _RUNAWAY_SCALE)
            if rise >= threshold:
                if (_runaway_fired_ms is None or
                        time.ticks_diff(now, _runaway_fired_ms) >= _SANITY_COOLDOWN):
                    print('[AC] Daikin runaway: room +{:.1f}F in {}min (threshold {:.1f}F), '
                          'gap={:.0f}F, outdoor {:.0f}F — resending off'.format(
                        rise, _SANITY_WINDOW_MS // 60000, threshold, gap, out_temp))
                    ir_temp = _boot_ir_temp if _boot_ir_temp is not None else current_temp
                    set_ir_command(False, ac_mode, ac_fan, ir_temp)
                    _runaway_fired_ms = now
                    _temp_history = []
