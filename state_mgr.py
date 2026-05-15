import json

STATE_FILE = "state.json"

def save_state(target_temp):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"target_temp": target_temp}, f)
    except Exception as e:
        print("Error saving state:", e)

def load_state(default_temp=72.0):
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        return data.get("target_temp", default_temp)
    except OSError:
        pass
    except Exception as e:
        print("Error loading state:", e)
    return default_temp
