#!/usr/bin/env python3
"""
remote.py — Wireless control for the ESP32 thermostat via WebREPL.

Commands:
    python3 remote.py upload file.py [file2.py ...]   upload file(s)
    python3 remote.py deploy                          upload all default files
    python3 remote.py reboot                          reboot device
    python3 remote.py settemp 74                      set target temperature
WebREPL runs from boot.py independently of main.py — works even if the
thermostat loop crashes.
"""

import sys
import os
import subprocess
import time

HOST     = '192.168.x.x'
PORT     = 8266
PASSWORD = os.environ.get('WEBREPL_PASSWORD')
if not PASSWORD:
    raise EnvironmentError('WEBREPL_PASSWORD environment variable is not set')
WS_URL   = 'ws://{}:{}/'.format(HOST, PORT)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WEBREPL_CLI = os.path.join(SCRIPT_DIR, 'webrepl_cli.py')

DEFAULT_FILES = [
    'boot.py',
    'main.py',
    'ac_mgr.py',
    'ir_daikin.py',
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


def _webrepl(args):
    """Run webrepl_cli.py with given args, return (returncode, output)."""
    cmd = [sys.executable, WEBREPL_CLI, '-p', PASSWORD] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, (result.stdout + result.stderr).strip()


def upload(fpath):
    fname = os.path.basename(fpath)
    rc, out = _webrepl([fpath, '{}:{}'.format(HOST, fname)])
    return rc == 0, out


def _http(method, path, body=None):
    """Simple HTTP call to the device API."""
    import http.client
    conn = http.client.HTTPConnection(HOST, 80, timeout=10)
    headers = {'Content-Length': str(len(body or b''))}
    conn.request(method, path, body=body or b'', headers=headers)
    try:
        resp = conn.getresponse()
        return resp.status, resp.read().decode()
    except Exception as e:
        return None, str(e)
    finally:
        conn.close()


def cmd_upload(files):
    ok = fail = 0
    for fpath in files:
        if not os.path.exists(fpath):
            print('  SKIP (not found): {}'.format(fpath))
            continue
        size = os.path.getsize(fpath)
        success, out = upload(fpath)
        if success:
            print('  OK  {:30s}  {:5d} bytes'.format(os.path.basename(fpath), size))
            ok += 1
        else:
            print('  FAIL {:30s}  {}'.format(os.path.basename(fpath), out))
            fail += 1
    print('\n{} uploaded, {} failed'.format(ok, fail))
    return fail == 0


def cmd_deploy():
    files = [f for f in DEFAULT_FILES if os.path.exists(f)]
    # Upload main.py last so thermostat keeps running during deploy
    ordered = [f for f in files if f != 'main.py'] + (['main.py'] if 'main.py' in files else [])
    success = cmd_upload(ordered)
    if success:
        print('Rebooting...')
        cmd_reboot()
    return success


def cmd_reboot():
    print('Rebooting device...')
    status, out = _http('POST', '/api/reboot')
    print('Done.' if status else 'Failed: ' + out)


def cmd_settemp(temp):
    try:
        t = float(temp)
    except ValueError:
        print('Invalid temperature: {}'.format(temp))
        sys.exit(1)
    status, out = _http('GET', '/api/command?cmd=set&val={}'.format(t))
    if status == 200:
        print('Target temp set to {}F'.format(t))
    else:
        print('Failed:', out)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd = args[0]
    rest = args[1:]

    if cmd == 'upload':
        if not rest:
            print('Usage: remote.py upload file.py [file2.py ...]')
            sys.exit(1)
        sys.exit(0 if cmd_upload(rest) else 1)

    elif cmd == 'deploy':
        sys.exit(0 if cmd_deploy() else 1)

    elif cmd == 'reboot':
        cmd_reboot()

    elif cmd == 'settemp':
        if not rest:
            print('Usage: remote.py settemp <temp>')
            sys.exit(1)
        cmd_settemp(rest[0])

    else:
        print('Unknown command: {}'.format(cmd))
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
