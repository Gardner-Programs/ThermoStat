"""
force_repl.py - Break into MicroPython REPL even when BaseException is caught.

Technique:
  1. First Ctrl+C interrupts _web_loop() -> caught by except BaseException -> gc.collect() runs (fast)
  2. Second Ctrl+C (50ms later) interrupts time.sleep(1) inside the except block
     -> NOT caught by the same except clause -> propagates -> crashes _web_loop_forever -> REPL!
"""
import serial
import time
import sys

PORT   = '/dev/ttyUSB0'
BAUD   = 115200
TARGET = sys.argv[1] if len(sys.argv) > 1 else 'boot.py'

print(f'[force_repl] Connecting to {PORT}...')
s = serial.Serial(PORT, BAUD, timeout=2)
time.sleep(0.3)
s.reset_input_buffer()

def read_all():
    data = b''
    while True:
        chunk = s.read(256)
        if not chunk:
            break
        data += chunk
    return data

read_all()  # drain

# Double Ctrl+C with 50ms gap to escape the BaseException handler's sleep
print('[force_repl] Sending double Ctrl+C...')
for attempt in range(8):
    s.write(b'\x03')
    time.sleep(0.05)   # let gc.collect() finish
    s.write(b'\x03')   # this one hits time.sleep(1) -> not caught
    time.sleep(0.3)
    resp = read_all()
    print(f'  attempt {attempt}: {repr(resp[:80])}')
    if b'>>>' in resp or b'raw REPL' in resp:
        print('[force_repl] Got REPL prompt!')
        break
else:
    # Try entering raw REPL anyway
    pass

# Enter raw REPL mode
print('[force_repl] Entering raw REPL...')
s.write(b'\x01')
time.sleep(0.4)
resp = read_all()
print('[force_repl] raw REPL response:', repr(resp[:100]))

if b'raw REPL' not in resp and b'>' not in resp:
    print('[force_repl] ERROR: Could not enter raw REPL. Try again or power cycle.')
    s.close()
    sys.exit(1)

# Read the target file content
with open(TARGET, 'rb') as f:
    content = f.read()

# Execute file write via raw REPL
print(f'[force_repl] Writing {TARGET} ({len(content)} bytes)...')
escaped = content.replace(b'\\', b'\\\\').replace(b"'", b"\\'").replace(b'\n', b'\\n').replace(b'\r', b'\\r')
cmd = b"f=open('" + TARGET.encode() + b"','wb');f.write(b'" + escaped + b"');f.close();print('OK')\r\n"

# Split into raw REPL command
s.write(cmd)
time.sleep(2)
resp = read_all()
print('[force_repl] Write response:', repr(resp[:200]))

if b'OK' in resp:
    print(f'[force_repl] SUCCESS: {TARGET} written!')
else:
    print('[force_repl] WARNING: Did not receive OK. Check device.')

# Exit raw REPL
s.write(b'\x02')
time.sleep(0.2)
s.close()
print('[force_repl] Done.')
