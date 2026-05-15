# boot.py — runs before main.py on every power-on
# Connects WiFi and starts WebREPL so file transfer works
# independently of main.py (survives crashes, no USB needed)

import network
import time

try:
    import secrets
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(secrets.WIFI['ssid'], secrets.WIFI['password'])
        for _ in range(15):
            if wlan.isconnected():
                break
            time.sleep(1)
    if wlan.isconnected():
        import webrepl
        webrepl.start(password=secrets.WEBREPL_PASSWORD)
        print('[boot] WebREPL ready on ws://{}:8266'.format(wlan.ifconfig()[0]))
    else:
        print('[boot] WiFi failed — WebREPL not started')
except Exception as e:
    print('[boot] error:', e)
