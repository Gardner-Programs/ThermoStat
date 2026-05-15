import network
import time
import secrets

def connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if not wlan.isconnected():
        print("Connecting to {}...".format(secrets.WIFI['ssid']))
        wlan.connect(secrets.WIFI['ssid'], secrets.WIFI['password'])
        timeout = 10
        while not wlan.isconnected() and timeout > 0:
            time.sleep(1)
            timeout -= 1

    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print("Wi-Fi connected: {}".format(ip))
        return ip

    print("Wi-Fi failed.")
    return None


def ensure_connected():
    """Reconnect if WiFi has dropped. Call periodically from the thermostat thread."""
    wlan = network.WLAN(network.STA_IF)
    if not wlan.isconnected():
        print('[WiFi] Connection lost — reconnecting...')
        connect()
