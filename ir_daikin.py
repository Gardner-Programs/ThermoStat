# ir_daikin.py — Daikin ARC433 IR transmitter for ESP32 MicroPython.

import machine
import utime

IR_TX_PIN = 23

_FREQ      = 38000
_PRE_MARK  = 420
_PRE_SPACE = 400
_PRE_GAP   = 25000
_HDR_MARK  = 3500
_HDR_SPACE = 1750
_BIT_MARK  = 420
_ONE_SPACE  = 1300
_ZERO_SPACE = 430

_MODE_MAP = {'auto': 0, 'dry': 2, 'cool': 3, 'heat': 4, 'fan': 6}
_FAN_MAP  = {'auto': 0xA, 'quiet': 0xB, 'low': 0x3,
             'mid': 0x4, 'high': 0x5, 'turbo': 0x7}

_pwm = None


def _get_pwm():
    global _pwm
    if _pwm is None:
        _pwm = machine.PWM(machine.Pin(IR_TX_PIN), freq=_FREQ, duty=0)
    return _pwm


def _mark(us):
    _get_pwm().duty(512)
    utime.sleep_us(us)


def _space(us):
    _get_pwm().duty(0)
    utime.sleep_us(us)


def _send_byte(val):
    for i in range(8):
        _mark(_BIT_MARK)
        _space(_ONE_SPACE if (val >> i) & 1 else _ZERO_SPACE)


def _send_once(frame):
    # Preamble burst
    for _ in range(5):
        _mark(_PRE_MARK)
        _space(_PRE_SPACE)
    _space(_PRE_GAP)

    # Header
    _mark(_HDR_MARK)
    _space(_HDR_SPACE)

    # Data
    for byte_val in frame:
        _send_byte(byte_val)

    # Final mark + idle
    _mark(_BIT_MARK)
    _get_pwm().duty(0)


def send(power, mode, temp_f, fan='auto', repeat=1):
    """Send Daikin IR command (repeated for reliability).

    power  : True = on,  False = off
    mode   : 'cool' | 'heat' | 'auto' | 'dry' | 'fan'
    temp_f : setpoint in °F (61–86)
    fan    : 'auto' | 'quiet' | 'low' | 'mid' | 'high' | 'turbo'
    """
    import machine
    mode_n = _MODE_MAP.get(mode, 3)   # default cool
    fan_n  = _FAN_MAP.get(fan, 0xA)   # default auto

    b5  = (mode_n << 4) | (1 if power else 0)
    b6  = max(0x21, min(0x3A, int(round(temp_f)) - 28))
    b8  = fan_n << 4
    b16 = 0x40 if not power else 0x00

    frame = [
        0x11, 0xDA, 0x27, 0x00, 0x00,
        b5, b6, 0x00, b8,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0xC5, b16, 0x00,
        0x00,  # checksum filled below
    ]
    frame[18] = sum(frame[:18]) & 0xFF

    for i in range(repeat):
        # Disable interrupts for the entire frame — utime.sleep_us() releases
        # the GIL in MicroPython, letting the web thread corrupt IR timing.
        irq = machine.disable_irq()
        try:
            _send_once(frame)
        finally:
            machine.enable_irq(irq)
        if i < repeat - 1:
            utime.sleep_ms(40)
