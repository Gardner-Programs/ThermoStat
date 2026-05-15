# ir_pelonis.py — Pelonis PSHO06MR6ASB IR control for ESP32 MicroPython
# Protocol : NEC, address 0x01, 38 kHz carrier
# Heater remembers last mode/temp on power cycle — only ON/OFF needed.
#
# Captured codes:
#   POWER  cmd=0x03  (toggle on/off)
#   TEMP + cmd=0x0D
#   TEMP - cmd=0x11
#
# Safeguards:
#   1. State persisted to pelonis_state.json — survives reboots
#   2. Boot-to-safe: if state says heater was ON at shutdown, send POWER
#      off immediately on import → known OFF state. System re-enables if needed.
#   3. Watchdog: call watchdog(current_temp) regularly while ON. If temp
#      not rising after WATCHDOG_MINS, assume IR missed → sets warning.

import machine
import utime
import json

IR_TX_PIN     = 23
WATCHDOG_MINS = 15

_STATE_FILE = 'pelonis_state.json'

# NEC timing (µs)
_NEC_HDR_MARK  = 9000
_NEC_HDR_SPACE = 4500
_NEC_BIT_MARK  =  560
_NEC_ONE_SPACE = 1690
_NEC_ZRO_SPACE =  560

_ADDR           = 0x01
_CMD_POWER      = 0x03

# ── State ─────────────────────────────────────────────────────────────────────
_heater_on      = False
_on_since_ticks = None
_temp_at_on     = None
_watchdog_fired = False
_warning        = None   # None or string message shown in web UI

_pwm = None


# ── Persistence ───────────────────────────────────────────────────────────────

def _load_state():
    global _heater_on
    try:
        with open(_STATE_FILE, 'r') as f:
            _heater_on = json.load(f).get('on', False)
    except OSError:
        pass
    except Exception as e:
        print('[Pelonis] State load error:', e)


def _save_state():
    try:
        with open(_STATE_FILE, 'w') as f:
            json.dump({'on': _heater_on}, f)
    except Exception as e:
        print('[Pelonis] State save error:', e)


# ── IR send ───────────────────────────────────────────────────────────────────

def _get_pwm():
    global _pwm
    if _pwm is None:
        _pwm = machine.PWM(machine.Pin(IR_TX_PIN), freq=38000, duty=0)
    return _pwm


def _mark(us):
    _get_pwm().duty(512)
    utime.sleep_us(us)


def _space(us):
    _get_pwm().duty(0)
    utime.sleep_us(us)


def _send_nec_once(cmd):
    frame = (_ADDR, _ADDR ^ 0xFF, cmd, cmd ^ 0xFF)
    _mark(_NEC_HDR_MARK)
    _space(_NEC_HDR_SPACE)
    for byte in frame:
        for i in range(8):
            _mark(_NEC_BIT_MARK)
            _space(_NEC_ONE_SPACE if (byte >> i) & 1 else _NEC_ZRO_SPACE)
    _mark(_NEC_BIT_MARK)
    _get_pwm().duty(0)

def _send_nec(cmd, repeat=1):
    for i in range(repeat):
        # Disable interrupts for the entire frame — utime.sleep_us() releases
        # the GIL in MicroPython, letting the web thread corrupt NEC timing.
        irq = machine.disable_irq()
        try:
            _send_nec_once(cmd)
        finally:
            machine.enable_irq(irq)
        if i < repeat - 1:
            utime.sleep_ms(40)  # gap between repeats


# ── Public API ────────────────────────────────────────────────────────────────

def turn_on(current_temp=None):
    global _heater_on, _on_since_ticks, _temp_at_on, _watchdog_fired, _warning
    _send_nec(_CMD_POWER)
    _heater_on      = True
    _on_since_ticks = utime.ticks_ms()
    _temp_at_on     = current_temp
    _watchdog_fired = False
    _warning        = None
    _save_state()
    print('[Pelonis] ON — backup heat activated' +
          (' at {:.1f}F'.format(current_temp) if current_temp else ''))


def turn_off():
    global _heater_on, _on_since_ticks, _temp_at_on, _watchdog_fired
    _send_nec(_CMD_POWER)
    _heater_on      = False
    _on_since_ticks = None
    _temp_at_on     = None
    _watchdog_fired = False
    _save_state()
    print('[Pelonis] OFF — backup heat deactivated')


def watchdog(current_temp):
    """Call regularly while heater is on. Sets warning if temp not rising."""
    global _watchdog_fired, _warning
    if not _heater_on or _on_since_ticks is None or _watchdog_fired:
        return
    elapsed_min = utime.ticks_diff(utime.ticks_ms(), _on_since_ticks) // 60000
    if elapsed_min < WATCHDOG_MINS:
        return
    if _temp_at_on is not None and current_temp <= _temp_at_on:
        msg = 'Backup heater ON {}min but temp not rising ({:.1f}F). Check IR / heater.'.format(
            elapsed_min, current_temp)
        print('[Pelonis] WARN:', msg)
        _warning = msg
        _watchdog_fired = True


def set_warning(msg):
    global _warning
    _warning = msg


def get_warning():
    return _warning


def clear_warning():
    global _warning
    _warning = None


def is_on():
    return _heater_on


# ── Boot-to-safe ──────────────────────────────────────────────────────────────
_load_state()
if _heater_on:
    print('[Pelonis] Boot-to-safe: was ON at shutdown — sending POWER off')
    _send_nec(_CMD_POWER)
    _heater_on = False
    _save_state()
