# ============================================================
# device2_sensors.py — App Lab script for Device 2 (Distance)
# ============================================================
# Deploy this on Arduino App Lab (tailmate2.local).
# It reads the Modulino Distance sensor every 5 seconds and
# serves the data at GET /sensors as JSON: {"distance": ...}
#
# Requirements:
#   pip install arduino-alvik flask
#
# Wiring: Modulino Distance connected via I2C to the Alvik board.
#
# Run: python3 device2_sensors.py
# ============================================================

from flask import Flask, jsonify
from flask_cors import CORS
import threading
import time

# Import Modulino Distance — available in App Lab environment
try:
    from modulino import ModulinoDistance
    distance_sensor = ModulinoDistance()
    MOCK = False
except Exception:
    MOCK = True

app = Flask(__name__)
CORS(app)

latest = {"distance": 0.0}


def read_loop():
    while True:
        try:
            if MOCK:
                latest["distance"] = 120.0
            else:
                latest["distance"] = round(distance_sensor.distance, 1)
        except Exception as e:
            print(f"Distance read error: {e}")
        time.sleep(5)


@app.route('/sensors')
def sensors():
    return jsonify(latest)


threading.Thread(target=read_loop, daemon=True).start()

if __name__ == '__main__':
    print("Device 2 (Distance) sensor server running on port 8000")
    app.run(host='0.0.0.0', port=8000)
