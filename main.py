import socket
import dht
import machine
import time
import json
import gc
import _thread
import log_mgr
log_mgr.setup()   # tee all print() to system.log from this point on
import wifi_mgr
import time_mgr
import web_ui
import ac_mgr
import state_mgr
import sched_mgr
import data_mgr
import learn_mgr
import outdoor_mgr

# --- HARDWARE ---
SENSOR_PIN = 26
sensor = dht.DHT22(machine.Pin(SENSOR_PIN))

# --- SHARED STATE (read/written by both threads) ---
# Primitive types — MicroPython GIL makes single reads/writes atomic
t_f              = 0.0
h                = 0.0
current_ac_state = 'IDLE'
target_temp      = state_mgr.load_state(default_temp=72.0)
away_mode        = False
away_heat_limit  = 58
away_cool_limit  = 84

# Signal: web thread sets True when override is cleared so thermostat
# thread re-evaluates the schedule immediately instead of waiting for
# the next minute tick (replaces the old last_minute_checked = -1 trick)
_recheck_schedule = False

# --- BOOT ---
ip_address = wifi_mgr.connect()

if not ip_address:
    print('[main] WiFi failed — rebooting in 30s')
    time.sleep(30)
    machine.reset()

wdt = machine.WDT(timeout=90000)
outdoor_mgr.set_wdt(wdt)

time_mgr.sync_time()
wdt.feed()

addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
_srv = socket.socket()
_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
_srv.bind(addr)
_srv.listen(1)
_srv.settimeout(0.5)

ac_mgr.boot_resend()
# Retry boot resend 2 more times with a short gap — first send often gets missed
# while the Daikin's IR receiver is still initialising or the unit is powering up.
time.sleep(2)
ac_mgr.boot_resend()
time.sleep(2)
ac_mgr.boot_resend()
print('\n--- SYSTEM READY (threaded) ---')
print('Dashboard: http://{}'.format(ip_address))
wdt.feed()

JSON_HDR = 'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n'


# ---------------------------------------------------------------------------
# JSON DATA BUILDER — called from web thread, reads shared globals
# ---------------------------------------------------------------------------
def _json_data():
    pw_js  = 'true'  if ac_mgr.ac_power           else 'false'
    aw_js  = 'true'  if away_mode                  else 'false'
    ovr_js = 'true'  if ac_mgr.is_override_active() else 'false'
    out    = outdoor_mgr.get_cached()  # web thread never triggers a blocking fetch
    out_js = '{:.1f}'.format(out) if out is not None else 'null'
    return (
        '{{"temp":{:.1f},"hum":{:.1f},"out":{},"target":{:.1f},'
        '"time":"{}","state":"{}","mode":"{}","fan":"{}","power":{},'
        '"away":{},"hl":{},"cl":{},"override":{}}}'
    ).format(t_f, h, out_js, target_temp, time_mgr.get_local_time_string(),
             current_ac_state, ac_mgr.ac_mode, ac_mgr.ac_fan, pw_js,
             aw_js, away_heat_limit, away_cool_limit, ovr_js)


# ---------------------------------------------------------------------------
# THERMOSTAT THREAD — sensor, schedule, brain, logging, WDT
# Runs on background core; never blocks the web server
# ---------------------------------------------------------------------------
def _thermostat_loop():
    global t_f, h, current_ac_state, target_temp
    global away_mode, away_heat_limit, away_cool_limit
    global _recheck_schedule

    last_read_time      = 0
    last_minute_checked = -1
    last_learn_day      = -1
    last_wifi_check     = 0
    last_ntp_check      = 0
    last_gc             = 0
    last_sanity_check   = 0

    while True:
        try:
            # 1. READ SENSOR (every 2.5 s)
            now_ticks = time.ticks_ms()
            if time.ticks_diff(now_ticks, last_read_time) > 2500:
                try:
                    sensor.measure()
                    t_f = (sensor.temperature() * 1.8) + 32
                    h   = sensor.humidity()
                    last_read_time = now_ticks
                except OSError:
                    pass

            # 2. GC (every 5 s — keeps heap clear; web thread allocates on every /api/data poll)
            if time.ticks_diff(now_ticks, last_gc) > 5000:
                last_gc = now_ticks
                gc.collect()

            # 3. WIFI + NTP MAINTENANCE (every 5 min)
            if time.ticks_diff(now_ticks, last_wifi_check) > 5 * 60 * 1000:
                last_wifi_check = now_ticks
                wifi_mgr.ensure_connected()
                if not time_mgr.is_synced():
                    time_mgr.sync_time()
                    wdt.feed()

            # 3. SCHEDULE LOGIC (once per minute, or immediately after override clear)
            if _recheck_schedule:
                last_minute_checked = -1
                _recheck_schedule   = False

            now = time_mgr.get_time_tuple()
            if now and now[4] != last_minute_checked:
                day_map          = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
                current_day      = day_map[now[6]]
                current_time_str = '{:02d}:{:02d}'.format(now[3], now[4])

                sched_state = sched_mgr.get_scheduled_state(current_day, current_time_str)

                if sched_state is not None:
                    # Away mode changes apply immediately (safety — not blocked by override)
                    new_away = sched_state['away']
                    new_hl   = sched_state['heat_limit']
                    new_cl   = sched_state['cool_limit']
                    if new_away != away_mode or new_hl != away_heat_limit or new_cl != away_cool_limit:
                        print('[{}] Away state: {} hl={} cl={}'.format(
                            current_time_str, new_away, new_hl, new_cl))
                        away_mode       = new_away
                        away_heat_limit = new_hl
                        away_cool_limit = new_cl

                    # Temp / mode changes blocked while manual override is active
                    if not ac_mgr.is_override_active():
                        new_temp = sched_state['temp']
                        new_mode = sched_state['mode']
                        if new_temp != target_temp or new_mode != ac_mgr.ac_mode:
                            print('[{}] Schedule: temp={} mode={}'.format(
                                current_time_str, new_temp, new_mode))
                            target_temp    = new_temp
                            ac_mgr.ac_mode = new_mode
                            state_mgr.save_state(target_temp)

                # Daily learning at 21:00
                if now[3] == 21 and now[4] == 0 and last_learn_day != now[2]:
                    last_learn_day = now[2]
                    learn_mgr.run()

                last_minute_checked = now[4]
                gc.collect()

            # 4. THERMOSTAT BRAIN
            if t_f > 0:
                out = outdoor_mgr.get_temp()
                current_ac_state = ac_mgr.check_and_update(
                    t_f, target_temp, away_mode, away_heat_limit, away_cool_limit)
                if time.ticks_diff(now_ticks, last_sanity_check) > 30000:
                    last_sanity_check = now_ticks
                    ac_mgr.check_sanity(t_f, out)

            # 5. DATA LOGGING (every 5 min)
            if t_f > 0:
                data_mgr.maybe_log(t_f, h, current_ac_state, outdoor_mgr.get_temp())

            wdt.feed()
            time.sleep(0.1)

        except Exception as e:
            try:
                print('[thermo] exception:', e)
            except Exception:
                pass
            wdt.feed()
            gc.collect()
            time.sleep(0.1)


# ---------------------------------------------------------------------------
# WEB SERVER (main thread) — always reachable, independent of thermostat
# ---------------------------------------------------------------------------
def _web_loop():
    global target_temp, _recheck_schedule

    while True:
        try:
            cl, _ = _srv.accept()
            cl.settimeout(5)
            request = cl.recv(2048).decode('utf-8')
            if not request:
                cl.close()
                continue

            # Read remaining body if Content-Length says there's more
            sep = request.find('\r\n\r\n')
            if sep != -1:
                cl_val = 0
                for hdr in request[:sep].split('\r\n'):
                    if hdr.lower().startswith('content-length:'):
                        try:
                            cl_val = int(hdr.split(':', 1)[1].strip())
                        except ValueError:
                            pass
                got = len(request) - sep - 4
                while got < cl_val:
                    chunk = cl.recv(min(cl_val - got, 512))
                    if not chunk:
                        break
                    request += chunk.decode('utf-8')
                    got += len(chunk)

            # --- API: GET DATA ---
            if '/api/data' in request:
                cl.send(JSON_HDR)
                cl.send(_json_data())

            # --- API: GET SCHEDULE ---
            elif 'GET /api/schedule' in request:
                cl.send(JSON_HDR)
                cl.send(json.dumps(sched_mgr.load_schedule()))

            # --- API: IR COMMAND (POST) — activates manual override ---
            elif 'POST /api/ac' in request:
                try:
                    body = request.split('\r\n\r\n')[1]
                    cmd  = json.loads(body)
                    ac_mgr.set_manual_override()
                    ac_mgr.set_ir_command(
                        cmd.get('power', False),
                        cmd.get('mode',  'cool'),
                        cmd.get('fan',   'auto'),
                        cmd.get('temp',  target_temp)
                    )
                    cl.send(JSON_HDR)
                    cl.send('{"ok":true}')
                except Exception as e:
                    print('[web/ac] error:', e)
                    cl.send('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n')

            # --- API: UPDATE SCHEDULE (POST) ---
            elif 'POST /api/schedule' in request:
                try:
                    body = request.split('\r\n\r\n')[1]
                    sched_mgr.save_schedule(json.loads(body))
                    cl.send('HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n')
                except Exception:
                    cl.send('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n')

            # --- API: MANUAL COMMANDS ---
            elif '/api/command' in request:
                if 'cmd=override_clear' in request:
                    ac_mgr.clear_override()
                    _recheck_schedule = True   # signal thermostat thread to re-evaluate now
                else:
                    state_changed = False
                    if 'cmd=set' in request:
                        try:
                            si = request.find('val=') + 4
                            ei = request.find(' ', si)
                            if ei == -1:
                                ei = len(request)
                            target_temp   = float(request[si:ei])
                            state_changed = True
                            ac_mgr.set_manual_override()   # pause schedule for 1 hour
                        except ValueError:
                            pass
                    if state_changed:
                        state_mgr.save_state(target_temp)
                cl.send(JSON_HDR)
                cl.send(_json_data())

            # --- API: REBOOT ---
            elif 'POST /api/reboot' in request:
                cl.send(JSON_HDR)
                cl.send('{"ok":true}')
                cl.close()
                machine.reset()

            # --- API: CLEAR LOG ---
            elif 'POST /api/log/clear' in request:
                try:
                    import os
                    if 'system.log' in os.listdir():
                        os.remove('system.log')
                    cl.send('HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n')
                    cl.send('{"ok":true}')
                    print('[log] Log cleared via web UI')
                except Exception as e:
                    print('[log] clear error:', e)
                    cl.send('HTTP/1.1 500 Internal Server Error\r\nConnection: close\r\n\r\n')

            # --- API: SYSTEM LOG ---
            elif 'GET /api/log' in request:
                import os
                try:
                    if 'system.log' in os.listdir():
                        cl.send('HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n')
                        with open('system.log', 'r') as f:
                            while True:
                                chunk = f.read(2048)
                                if not chunk:
                                    break
                                cl.send(chunk)
                                wdt.feed()
                    else:
                        cl.send('HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n')
                        cl.send('No log yet.')
                except Exception as e:
                    print('[log] error:', e)
                    cl.send('HTTP/1.1 500 Internal Server Error\r\nConnection: close\r\n\r\n')

            # --- API: HISTORY DATA ---
            elif 'GET /api/history' in request:
                import os
                try:
                    if 'history.csv' in os.listdir():
                        cl.send('HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n')
                        with open('history.csv', 'r') as f:
                            while True:
                                chunk = f.read(2048)
                                if not chunk:
                                    break
                                cl.send(chunk)
                                wdt.feed()
                    else:
                        cl.send('HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n')
                except Exception as e:
                    print('[history] error:', e)
                    cl.send('HTTP/1.1 500 Internal Server Error\r\nConnection: close\r\n\r\n')

            # --- FULL PAGE LOAD ---
            else:
                cl.send('HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n')
                with open('_web_p1.html', 'r') as f:
                    while True:
                        chunk = f.read(2048)
                        if not chunk:
                            break
                        cl.send(chunk)
                        wdt.feed()
                cl.send(web_ui.get_init(target_temp, ac_mgr.ac_mode, ac_mgr.ac_fan, ac_mgr.ac_power))
                with open('_web_p2.html', 'r') as f:
                    while True:
                        chunk = f.read(2048)
                        if not chunk:
                            break
                        cl.send(chunk)
                        wdt.feed()

            cl.close()
            gc.collect()

        except OSError:
            pass
        except Exception as e:
            print('[web] exception:', e)
            gc.collect()


def _web_loop_forever():
    while True:
        try:
            _web_loop()
        except Exception as e:
            print('[web] loop exited ({}), restarting'.format(e))
            gc.collect()
            time.sleep(1)


# --- START THREADS ---
_thread.start_new_thread(_thermostat_loop, ())
_web_loop_forever()   # main thread
