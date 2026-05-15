"""
ir_capture.py — One-shot IR remote capture tool.

Wiring (VS1838B / TSOP38238):
  VCC  → 3.3V
  GND  → GND
  OUT  → GPIO 22

Run standalone:  mpremote run ir_capture.py
Press a button on the Pelonis remote when prompted.
Repeat for each button needed: Power, HI, LO, ECO, Temp Up, Temp Down.
Copy the printed hex codes into ir_pelonis.py.
"""

import machine
import utime

IR_RX_PIN  = 22      # change if using a different GPIO
MAX_PULSES = 200     # enough for any protocol
IDLE_US    = 10000   # gap this long = end of burst


def capture_one(label):
    pin   = machine.Pin(IR_RX_PIN, machine.Pin.IN)
    buf   = []
    start = [0]

    def handler(p):
        now = utime.ticks_us()
        if start[0]:
            buf.append(utime.ticks_diff(now, start[0]))
        start[0] = now

    print("\n[{}] Point remote at receiver and press button...".format(label))
    pin.irq(trigger=machine.Pin.IRQ_RISING | machine.Pin.IRQ_FALLING,
            handler=handler)

    # Wait for burst then idle gap
    deadline = utime.ticks_ms() + 8000          # 8 s timeout
    while utime.ticks_diff(deadline, utime.ticks_ms()) > 0:
        if len(buf) > 10:
            # Check for idle gap (no new edges for IDLE_US)
            utime.sleep_us(IDLE_US)
            prev = len(buf)
            utime.sleep_us(IDLE_US)
            if len(buf) == prev:
                break
        utime.sleep_ms(5)

    pin.irq(handler=None)

    if len(buf) < 10:
        print("  Nothing captured — try again")
        return None

    raw = buf[:]
    print("  Raw pulses ({} edges):".format(len(raw)))
    print("  ", raw[:40], "..." if len(raw) > 40 else "")

    # --- Try NEC decode ---
    nec = _decode_nec(raw)
    if nec is not None:
        addr, cmd = nec
        print("  NEC decoded: addr=0x{:02X} cmd=0x{:02X}  raw32=0x{:08X}".format(
            addr, cmd, (addr << 24) | ((addr ^ 0xFF) << 16) | (cmd << 8) | (cmd ^ 0xFF)))
    else:
        print("  NEC decode failed — raw timings are still usable")

    # Always print compact hex of pulse widths for manual use
    compact = ','.join(str(v) for v in raw)
    print("  PULSES: " + compact)
    return raw


def _decode_nec(pulses):
    """Attempt NEC protocol decode. Returns (addr, cmd) or None."""
    try:
        if not (8000 < pulses[0] < 10000):
            return None
        if not (4000 < pulses[1] < 5000):
            return None
        bits = []
        i = 2
        while i + 1 < len(pulses) and len(bits) < 32:
            mark  = pulses[i]
            space = pulses[i + 1]
            if not (400 < mark < 800):
                break
            if 400 < space < 800:
                bits.append(0)
            elif 1400 < space < 1800:
                bits.append(1)
            else:
                break
            i += 2
        if len(bits) < 32:
            return None
        def bits_to_byte(b): return sum(b[j] << j for j in range(8))
        addr = bits_to_byte(bits[0:8])
        cmd  = bits_to_byte(bits[16:24])
        return addr, cmd
    except Exception:
        return None


# ── Main capture session ──────────────────────────────────────────────────────
print("=" * 50)
print("  Pelonis IR Code Capture")
print("  Receiver on GPIO {}".format(IR_RX_PIN))
print("=" * 50)

buttons = [
    "TIMER 2H",
]

results = {}
for label in buttons:
    raw = capture_one(label)
    results[label] = raw
    utime.sleep(1)

print("\n" + "=" * 50)
print("  Capture complete — copy output above into ir_pelonis.py")
print("=" * 50)
