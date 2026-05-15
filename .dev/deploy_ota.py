#!/usr/bin/env python3
"""
deploy_ota.py — Upload files to the ESP32 thermostat over WiFi (no USB needed).

Usage:
    python3 deploy_ota.py              # upload all default files
    python3 deploy_ota.py main.py      # upload specific file(s)
    python3 deploy_ota.py ac_mgr.py ir_pelonis.py

If main.py is in the upload list it is uploaded first, then the device is
rebooted so the new streaming OTA endpoint is live before uploading larger files.
"""

import sys
import os
import time
import http.client

# ── CONFIG ─────────────────────────────────────────────────────────────────────
HOST         = '192.168.x.x'   # ESP32 thermostat
PORT         = 80
TIMEOUT      = 60               # seconds per file upload
REBOOT_WAIT  = 12               # seconds to wait for device to come back after reboot
# ──────────────────────────────────────────────────────────────────────────────

# Files uploaded by default (same set as USB deploy, minus data/log files)
DEFAULT_FILES = [
    'main.py',
    'ac_mgr.py',
    'ir_pelonis.py',
    'web_ui.py',
    '_web_p1.html',
    '_web_p2.html',
    'wifi_mgr.py',
    'time_mgr.py',
    'state_mgr.py',
    'sched_mgr.py',
    'data_mgr.py',
    'learn_mgr.py',
    'outdoor_mgr.py',
    'log_mgr.py',
]


def upload(host, fpath):
    fname = os.path.basename(fpath)
    with open(fpath, 'rb') as f:
        data = f.read()
    conn = http.client.HTTPConnection(host, PORT, timeout=TIMEOUT)
    conn.request(
        'POST',
        '/ota/upload?file={}'.format(fname),
        body=data,
        headers={
            'Content-Type': 'application/octet-stream',
            'Content-Length': str(len(data)),
        },
    )
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, body


def reboot(host):
    conn = http.client.HTTPConnection(host, PORT, timeout=10)
    conn.request('POST', '/api/reboot', body=b'', headers={'Content-Length': '0'})
    try:
        conn.getresponse()
    except Exception:
        pass  # device resets immediately, response may not arrive
    conn.close()


def wait_for_device(host, timeout=30):
    """Poll until device responds or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            conn = http.client.HTTPConnection(host, PORT, timeout=3)
            conn.request('GET', '/api/data')
            resp = conn.getresponse()
            conn.close()
            if resp.status == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def do_upload(fpath, ok, fail):
    try:
        status, body = upload(HOST, fpath)
        if status == 200:
            size = os.path.getsize(fpath)
            print('  OK  {:30s}  {:5d} bytes'.format(os.path.basename(fpath), size))
            return ok + 1, fail
        else:
            print('  FAIL {:30s}  HTTP {} — {}'.format(os.path.basename(fpath), status, body))
            return ok, fail + 1
    except Exception as e:
        print('  ERR  {:30s}  {}'.format(os.path.basename(fpath), e))
        return ok, fail + 1


def main():
    # Filter to existing files, preserving order but putting main.py first
    existing = []
    skipped  = []
    for f in (sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_FILES):
        if os.path.exists(f):
            existing.append(f)
        else:
            skipped.append(f)
    for f in skipped:
        print('  SKIP (not found): {}'.format(f))

    # If main.py is in the list, upload it first then reboot so the new
    # streaming OTA endpoint is active before uploading larger files
    needs_reboot = 'main.py' in existing
    if needs_reboot:
        existing = ['main.py'] + [f for f in existing if f != 'main.py']

    ok = fail = 0

    if needs_reboot and existing:
        print('Uploading main.py first (will reboot before remaining files)...')
        ok, fail = do_upload('main.py', ok, fail)
        remaining = [f for f in existing if f != 'main.py']
        if remaining and ok > 0:
            print('Rebooting device to activate new OTA endpoint...')
            reboot(HOST)
            print('Waiting for device to come back up...', end='', flush=True)
            if wait_for_device(HOST, timeout=30):
                print(' ready.')
            else:
                print(' timeout — device may still be booting, continuing anyway.')
            for fpath in remaining:
                ok, fail = do_upload(fpath, ok, fail)
        elif remaining:
            for fpath in remaining:
                ok, fail = do_upload(fpath, ok, fail)
    else:
        for fpath in existing:
            ok, fail = do_upload(fpath, ok, fail)

    print('\n{} uploaded, {} failed'.format(ok, fail))
    if fail:
        sys.exit(1)

    print('Rebooting device...')
    reboot(HOST)
    print('Done — device is restarting.')


if __name__ == '__main__':
    main()
