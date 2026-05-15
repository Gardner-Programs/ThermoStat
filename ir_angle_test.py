#!/usr/bin/env python3
"""
ir_angle_test.py — Repeatedly sends IR on command every 10 seconds.
Watch/listen for the Pelonis to beep each cycle while adjusting LED angle.
Ctrl+C to stop (leaves heater off).
"""

import http.client
import time
import sys

HOST = '192.168.x.x'

def post(path):
    try:
        conn = http.client.HTTPConnection(HOST, 80, timeout=5)
        conn.request('POST', path, body=b'', headers={'Content-Length': '0'})
        resp = conn.getresponse()
        conn.close()
        return resp.status == 200
    except Exception as e:
        print('  error:', e)
        return False

print('IR angle test — Pelonis should beep every cycle if signal is received.')
print('Adjust LED angle until you get consistent beeps. Ctrl+C to stop.\n')

cycle = 1
try:
    while True:
        print('Cycle {} — sending OFF...'.format(cycle), end=' ', flush=True)
        post('/api/heater/off')
        time.sleep(5)   # give device time to finish IR send and feed WDT
        print('ON...', end=' ', flush=True)
        post('/api/heater/on')
        print('sent. Listen for beep. Next in 20s.')
        time.sleep(20)  # longer gap so device stays healthy between cycles
        cycle += 1
except KeyboardInterrupt:
    print('\nStopping — sending heater off.')
    post('/api/heater/off')
