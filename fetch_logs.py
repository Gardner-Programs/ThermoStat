#!/usr/bin/env python3
"""
fetch_logs.py — Pull logs and history from the ESP32 thermostat and archive locally.

Usage:
    python3 fetch_logs.py           # fetch system.log + history.csv
    python3 fetch_logs.py --log     # system.log only
    python3 fetch_logs.py --hist    # history.csv only
    python3 fetch_logs.py --tail 50 # print last N lines of log after fetching

Archives are saved to ./logs/ with timestamps so nothing is ever overwritten.
"""

import sys
import os
import http.client
from datetime import datetime

HOST    = '192.168.x.x'
PORT    = 80
TIMEOUT = 30
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')


def fetch(host, path):
    conn = http.client.HTTPConnection(host, PORT, timeout=TIMEOUT)
    conn.request('GET', path)
    resp = conn.getresponse()
    if resp.status != 200:
        conn.close()
        raise RuntimeError('HTTP {}'.format(resp.status))
    data = resp.read()
    conn.close()
    return data.decode('utf-8', errors='replace')


def save(content, filename):
    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    base, ext = os.path.splitext(filename)
    archived = os.path.join(LOG_DIR, '{}__{}{}'.format(base, stamp, ext))
    latest   = os.path.join(LOG_DIR, filename)          # always-current symlink/copy

    with open(archived, 'w') as f:
        f.write(content)

    # Overwrite the "latest" copy for easy reference
    with open(latest, 'w') as f:
        f.write(content)

    return archived


def tail(text, n):
    lines = text.splitlines()
    return '\n'.join(lines[-n:])


def main():
    args = sys.argv[1:]
    do_log  = '--log'  in args or not any(a.startswith('--') for a in args if a != '--tail')
    do_hist = '--hist' in args or not any(a.startswith('--') for a in args if a != '--tail')

    # --tail N
    tail_n = None
    if '--tail' in args:
        idx = args.index('--tail')
        try:
            tail_n = int(args[idx + 1])
        except (IndexError, ValueError):
            tail_n = 40

    # If only --tail was passed with no --log/--hist, default to log only
    if args and all(a in ('--tail', str(tail_n)) for a in args):
        do_log  = True
        do_hist = False

    if do_log:
        print('Fetching system.log ...')
        try:
            content = fetch(HOST, '/api/log')
            path = save(content, 'system.log')
            lines = len(content.splitlines())
            print('  saved → {}  ({} lines)'.format(path, lines))
            if tail_n:
                print('\n--- last {} lines ---'.format(tail_n))
                print(tail(content, tail_n))
                print('---')
        except Exception as e:
            print('  ERROR: {}'.format(e))

    if do_hist:
        print('Fetching history.csv ...')
        try:
            content = fetch(HOST, '/api/history')
            path = save(content, 'history.csv')
            lines = len(content.splitlines())
            print('  saved → {}  ({} lines)'.format(path, lines))
        except Exception as e:
            print('  ERROR: {}'.format(e))


if __name__ == '__main__':
    main()
