"""
ir_test.py — Sweep candidate NEC codes to find the Pelonis TIMER button.

Run:  mpremote run ir_test.py

Watch the heater's display. When the timer indicator lights up, note which
code was just printed. Enter that code as _CMD_TIMER_2H in ir_pelonis.py.

Known codes (for reference):
  POWER  = 0x03
  MODE   = 0x0F
  TEMP+  = 0x0D
  TEMP-  = 0x11
"""

import machine
import utime

IR_TX_PIN     = 23
_NEC_HDR_MARK  = 9000
_NEC_HDR_SPACE = 4500
_NEC_BIT_MARK  =  560
_NEC_ONE_SPACE = 1690
_NEC_ZRO_SPACE =  560
_ADDR = 0x01

_pwm = machine.PWM(machine.Pin(IR_TX_PIN), freq=38000, duty=0)

def _mark(us):
    _pwm.duty(512)
    utime.sleep_us(us)

def _space(us):
    _pwm.duty(0)
    utime.sleep_us(us)

def _send_nec(cmd):
    frame = (_ADDR, _ADDR ^ 0xFF, cmd, cmd ^ 0xFF)
    _mark(_NEC_HDR_MARK)
    _space(_NEC_HDR_SPACE)
    for byte in frame:
        for i in range(8):
            _mark(_NEC_BIT_MARK)
            _space(_NEC_ONE_SPACE if (byte >> i) & 1 else _NEC_ZRO_SPACE)
    _mark(_NEC_BIT_MARK)
    _pwm.duty(0)

# Candidate timer codes — gaps between known buttons
CANDIDATES = [0x01, 0x05, 0x07, 0x09, 0x0B, 0x13, 0x15, 0x17, 0x19, 0x1B, 0x1D, 0x21]

print('Pelonis timer code sweep')
print('Watch the heater display — note which code makes the timer appear.')
print('Pause 4s between each send.\n')

for code in CANDIDATES:
    print('Sending 0x{:02X} ...'.format(code))
    _send_nec(code)
    utime.sleep_ms(4000)

print('\nDone. Paste the matching code into ir_pelonis.py as _CMD_TIMER_2H.')
