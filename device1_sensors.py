# ============================================================
# device1_sensors.py — App Lab script for Device 1 (Thermo)
# ============================================================
# Deploy this on Arduino App Lab (tailmate.local).
# It reads the Modulino Thermo sensor every 5 seconds and
# serves the data at GET /sensors as JSON: {"temp": ..., "humidity": ...}
#
# Requirements:
#   pip install arduino-alvik flask
#
# Wiring: Modulino Thermo connected via I2C to the Alvik board.
#
# Run: python3 device1_sensors.py
# ============================================================

from flask import Flask, jsonify
from flask_cors import CORS
import threading
import time

# Import Modulino Thermo — available in App Lab environment
try:
    from modulino import ModulinoThermo
    thermo = ModulinoThermo()
    MOCK = False
except Exception:
    MOCK = True

app = Flask(__name__)
CORS(app)

latest = {"temp": 0.0, "humidity": 0.0}


def read_loop():
    while True:
        try:
            if MOCK:
                latest["temp"] = 22.5
                latest["humidity"] = 55.0
            else:
                latest["temp"] = round(thermo.temperature, 1)
                latest["humidity"] = round(thermo.humidity, 1)
        except Exception as e:
            print(f"Thermo read error: {e}")
        time.sleep(5)


@app.route('/sensors')
def sensors():
    return jsonify(latest)


threading.Thread(target=read_loop, daemon=True).start()

if __name__ == '__main__':
    print("Device 1 (Thermo) sensor server running on port 8000")
    app.run(host='0.0.0.0', port=8000)
