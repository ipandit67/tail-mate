import cv2
import io
import os
import pandas as pd
import json
import queue
import threading
import numpy as np
from flask import Flask, request, jsonify, render_template_string, Response, stream_with_context
from flask_cors import CORS
from datetime import datetime
import google.generativeai as genai
import PIL.Image  # For handling the photo
from supabase import create_client


app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)


@app.route('/')
@app.route('/index.html')
def serve_index():
    return app.send_static_file('index.html')

# --- SUPABASE CONFIGURATION ---
SUPABASE_URL = "https://kazkfrgbnsatagfckjpa.supabase.co"
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImthemtmcmdibnNhdGFnZmNranBhIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NjU0Nzg3NiwiZXhwIjoyMDkyMTIzODc2fQ.Z40rSTsoQufUqM81ICBCxEIGab9cL88sS8t1eRrO8W8"
supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# --- REVIEW QUEUE ---
review_queue = queue.Queue()
current_review = {"state": "awaiting"}

# --- SESSION EVENTS (in-memory, for /events endpoint) ---
session_events = []

# --- LATEST SENSOR READINGS ---
latest_sensors = {"temp": 0, "humidity": 0, "distance": 0, "movement": 0}

# --- GEMINI CONFIGURATION ---
genai.configure(api_key="AIzaSyDgZ5Ge4rtpnopI0QCqKRtgMb18uzUo12U")
model = genai.GenerativeModel('gemini-1.5-flash')

@app.route('/upload_capture', methods=['POST'])
def handle_arduino_trigger():
    global current_review
    # 1. Get the image from the Arduino/Laptop Camera
    img_file = request.files['image']
    img = PIL.Image.open(img_file)

    temp = float(request.form.get('temp', 0))
    humidity = float(request.form.get('humidity', 0))
    distance = float(request.form.get('distance', 0))

    # Push processing state immediately
    current_review = {
        "state": "processing",
        "temp": temp,
        "humidity": humidity,
        "distance": distance,
    }
    review_queue.put(current_review.copy())

    # 2. ASK THE BRAIN (The Gemini API call)
    prompt = "Identify this San Diego reptile. Give me ONLY the common name."
    response = model.generate_content([prompt, img])
    detected_species = response.text.strip() # Example: "Coast Horned Lizard"
    lat = float(request.form.get('lat', 32.880))
    lon = float(request.form.get('lon', -117.235))

    # 3. USE YOUR DATA (The Dictionary check you already have)
    # This checks the Gemini result against your observations-712033.csv
    alert_color, status_text, habitat_ok = analyze_capture(detected_species, lat, lon)

    VENOMOUS_SPECIES = {"Red Diamond Rattlesnake", "Southern Pacific Rattlesnake"}
    is_venomous = detected_species in VENOMOUS_SPECIES

    approachability_color = "#4A7C59" if alert_color == "GREEN" else ("#E8923A" if status_text == "Common" else "#C0392B")

    # Push result state
    current_review = {
        "state": "result",
        "species": detected_species,
        "alert": alert_color,
        "status": status_text,
        "habitat_match": habitat_ok,
        "venomous": is_venomous,
        "approachability_color": approachability_color,
        "confidence": 92,
        "lat": lat,
        "lon": lon,
        "temp": temp,
        "humidity": humidity,
        "distance": distance,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    review_queue.put(current_review.copy())

    # 4. SEND RESPONSE BACK TO ARDUINO
    return jsonify({
        "species": detected_species,
        "alert": alert_color,
        "status": status_text
    })
# --- VIDEO FEED STATE ---
_motion_flash_until = 0


# ==========================================
# 1. DATA LAYER
# ==========================================
def build_reference_db(csv_path):
    df = pd.read_csv(csv_path)

    status_map = {
        "Coast Horned Lizard": "Threatened (CA)",
        "Orange-throated Whiptail": "Vulnerable",
        "Red Diamond Rattlesnake": "Habitat Alert / Concern",
        "Western Fence Lizard": "Common / Safe",
        "Southern Pacific Rattlesnake": "Common"
    }

    ranges = df.groupby('common_name').agg({
        'latitude': ['min', 'max'],
        'longitude': ['min', 'max']
    })
    ranges.columns = ['lat_min', 'lat_max', 'lon_min', 'lon_max']

    db = {}
    for species in ranges.index:
        db[species] = {
            "bounds": ranges.loc[species].to_dict(),
            "status": status_map.get(species, "Unknown")
        }
    return db


SPECIES_DB = build_reference_db('observations-712033.csv')

VENOMOUS_SPECIES = {"Red Diamond Rattlesnake", "Southern Pacific Rattlesnake"}


# ==========================================
# 2. ML & LOGIC LAYER
# ==========================================
def fuzzy_lookup(species_name):
    name_lower = species_name.lower()
    # Exact case-insensitive match
    for key in SPECIES_DB:
        if key.lower() == name_lower:
            return SPECIES_DB[key]
    # Substring match in either direction
    for key in SPECIES_DB:
        if key.lower() in name_lower or name_lower in key.lower():
            return SPECIES_DB[key]
    return None


def analyze_capture(species_name, lat, lon):
    metadata = fuzzy_lookup(species_name)

    if not metadata:
        return "RED", "Unknown Species", False

    is_endangered = any(word in metadata['status'] for word in ["Threatened", "Vulnerable", "Endangered"])

    b = metadata['bounds']
    in_habitat = (b['lat_min'] - 0.01 <= lat <= b['lat_max'] + 0.01) and \
                 (b['lon_min'] - 0.01 <= lon <= b['lon_max'] + 0.01)

    alert = "RED" if (is_endangered or not in_habitat) else "GREEN"

    return alert, metadata['status'], in_habitat


def get_approachability(is_venomous, alert, habitat_ok, status_text):
    if is_venomous or (alert == "RED" and not habitat_ok):
        return "Do Not Approach"
    is_endangered = any(word in status_text for word in ["Threatened", "Vulnerable", "Endangered"])
    if is_endangered or not habitat_ok:
        return "Observe from Distance"
    return "Approach Safely"


def generate_species_notes(species_name):
    try:
        prompt = f"Write a brief 2-3 sentence field note about {species_name} in San Diego. Focus on identifying features and behavior. Keep it under 150 characters."
        response = client.models.generate_content(model='gemini-1.5-flash', contents=[prompt])
        return response.text.strip()
    except Exception as e:
        return f"Unable to generate notes: {str(e)}"


# ==========================================
# 3. API ENDPOINTS
# ==========================================

@app.route('/sensors', methods=['POST'])
def sensors():
    global latest_sensors
    latest_sensors = {
        "temp": float(request.form.get('temp', latest_sensors['temp'])),
        "humidity": float(request.form.get('humidity', latest_sensors['humidity'])),
        "distance": float(request.form.get('distance', latest_sensors['distance'])),
        "movement": float(request.form.get('movement', latest_sensors['movement'])),
    }
    return jsonify({"success": True})


@app.route('/observations', methods=['GET'])
def observations():
    result = supabase_client.table("observations").select("*").order("timestamp", desc=True).execute()
    return jsonify(result.data)


@app.route('/events', methods=['GET'])
def events():
    return jsonify(session_events)


@app.route('/upload_capture', methods=['POST'])
def handle_arduino_trigger():
    global current_review, _motion_flash_until

    img_file = request.files['image']
    img_bytes = img_file.read()
    img = PIL.Image.open(io.BytesIO(img_bytes))

    temp = float(request.form.get('temp') or latest_sensors['temp'])
    humidity = float(request.form.get('humidity') or latest_sensors['humidity'])
    distance = float(request.form.get('distance') or latest_sensors['distance'])

    current_review = {
        "state": "processing",
        "temp": temp,
        "humidity": humidity,
        "distance": distance,
    }
    review_queue.put(current_review.copy())

    # Save image to /tmp and upload to Supabase Storage
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    capture_path = "/tmp/capture.jpg"
    storage_filename = f"captures/{ts}.jpg"

    with open(capture_path, "wb") as f:
        f.write(img_bytes)

    image_url = ""
    try:
        with open(capture_path, "rb") as f:
            supabase_client.storage.from_("captures").upload(
                storage_filename,
                f.read(),
                {"content-type": "image/jpeg"}
            )
        image_url = f"{SUPABASE_URL}/storage/v1/object/public/captures/{storage_filename}"
    except Exception as e:
        print(f"Storage upload failed: {e}")

    # Signal video feed to flash red for 2 seconds
    _motion_flash_until = __import__('time').time() + 2

    prompt = "Identify this San Diego reptile. Give me ONLY the common name."
    response = client.models.generate_content(model='gemini-1.5-flash', contents=[prompt, img])
    detected_species = response.text.strip()
    lat = float(request.form.get('lat', 32.880))
    lon = float(request.form.get('lon', -117.235))

    alert_color, status_text, habitat_ok = analyze_capture(detected_species, lat, lon)

    is_venomous = detected_species in VENOMOUS_SPECIES
    approachability = get_approachability(is_venomous, alert_color, habitat_ok, status_text)
    approachability_color = "#4A7C59" if alert_color == "GREEN" else ("#E8923A" if status_text == "Common" else "#C0392B")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    notes = generate_species_notes(detected_species)

    current_review = {
        "state": "result",
        "species": detected_species,
        "alert": alert_color,
        "status": status_text,
        "habitat_match": habitat_ok,
        "venomous": is_venomous,
        "approachability": approachability,
        "approachability_color": approachability_color,
        "confidence": 92,
        "lat": lat,
        "lon": lon,
        "temp": temp,
        "humidity": humidity,
        "distance": distance,
        "timestamp": timestamp,
        "image_url": image_url,
        "notes": notes,
    }
    review_queue.put(current_review.copy())

    session_events.append({
        "species": detected_species,
        "timestamp": timestamp,
        "approachability": approachability,
        "confidence": 92,
        "image_url": image_url,
        "notes": notes,
    })

    return jsonify({
        "species": detected_species,
        "alert": alert_color,
        "status": status_text
    })


@app.route('/video_feed')
def video_feed():
    def generate():
        import time
        # Open a dedicated camera for this stream — not shared with other threads
        cam = None
        for index in [0, 1, 2]:
            cap = cv2.VideoCapture(index)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                cam = cap
                break

        if cam is None:
            return

        prev_gray = None
        try:
            while True:
                ret, frame = cam.read()
                if not ret:
                    time.sleep(0.1)
                    continue

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (21, 21), 0)

                if prev_gray is not None:
                    diff = cv2.absdiff(prev_gray, gray)
                    _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
                    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    for c in contours:
                        if cv2.contourArea(c) > 500:
                            x, y, w, h = cv2.boundingRect(c)
                            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

                if time.time() < _motion_flash_until:
                    cv2.rectangle(frame, (0, 0), (frame.shape[1], frame.shape[0]), (0, 0, 255), 8)

                prev_gray = gray

                _, jpeg = cv2.imencode('.jpg', frame)
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
        finally:
            cam.release()

    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')


REVIEW_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0"/>
<title>TailMate Review</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700&family=Roboto+Condensed:wght@400;700&display=swap" rel="stylesheet"/>
<style>
  *{box-sizing:border-box;margin:0;padding:0;}
  html,body{height:100%;background:#F5F0E8;font-family:'Roboto Condensed',sans-serif;color:#2C3E2D;}
  #app{min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px;text-align:center;}
  .emoji{font-size:80px;margin-bottom:16px;}
  .awaiting-title{font-family:'Playfair Display',serif;font-size:28px;color:#2C3E2D;}
  @keyframes pulse{0%,100%{opacity:1;}50%{opacity:0.4;}}
  .pulse{animation:pulse 2s infinite;}
  .spinner{width:48px;height:48px;border:5px solid #ddd;border-top-color:#4A6B8A;border-radius:50%;animation:spin 0.9s linear infinite;margin:0 auto 20px;}
  @keyframes spin{to{transform:rotate(360deg);}}
  .processing-title{font-size:22px;color:#4A6B8A;margin-bottom:16px;}
  .sensor-box{background:#fff;border-radius:12px;padding:16px 24px;margin-top:8px;font-size:15px;color:#4A6B8A;line-height:2;}
  .species-name{font-size:48px;font-weight:700;color:#2C3E2D;margin-bottom:12px;line-height:1.1;}
  .badge{display:inline-block;padding:10px 28px;border-radius:50px;font-size:26px;font-weight:700;color:#fff;margin-bottom:16px;}
  .venomous-warn{background:#C0392B;color:#fff;border-radius:8px;padding:10px 20px;font-size:18px;font-weight:700;margin-bottom:12px;}
  .info-row{font-size:16px;margin:6px 0;color:#2C3E2D;}
  .confidence{font-size:32px;font-weight:700;color:#4A6B8A;margin:12px 0;}
  .btn-row{display:flex;gap:16px;margin-top:24px;width:100%;max-width:400px;}
  .btn{flex:1;padding:18px;border:none;border-radius:14px;font-family:'Roboto Condensed',sans-serif;font-size:20px;font-weight:700;cursor:pointer;color:#fff;}
  .btn-confirm{background:#4A7C59;}
  .btn-reject{background:#C0392B;}
</style>
</head>
<body>
<div id="app">
  <div class="emoji pulse">🐍</div>
  <div class="awaiting-title">Awaiting capture…</div>
</div>
<script>
const app = document.getElementById('app');
const es = new EventSource('/stream');
es.onmessage = function(e) {
  const d = JSON.parse(e.data);
  if(d.state === 'awaiting') {
    app.innerHTML = `<div class="emoji pulse">🐍</div><div class="awaiting-title">Awaiting capture…</div>`;
  } else if(d.state === 'processing') {
    app.innerHTML = `
      <div class="spinner"></div>
      <div class="processing-title">Identifying species…</div>
      <div class="sensor-box">
        🌡️ Temp: <b>${d.temp}°C</b><br>
        💧 Humidity: <b>${d.humidity}%</b><br>
        📏 Distance: <b>${d.distance} cm</b>
      </div>`;
  } else if(d.state === 'result') {
    const venomHtml = d.venomous ? `<div class="venomous-warn">⚠️ VENOMOUS</div>` : '';
    app.innerHTML = `
      <div class="species-name">${d.species}</div>
      <div class="badge" style="background:${d.approachability_color}">${d.approachability}</div>
      ${venomHtml}
      <div class="info-row">📋 Conservation: <b>${d.status}</b></div>
      <div class="info-row">🗺️ Habitat Match: <b>${d.habitat_match ? 'Yes' : 'No (Outside Range)'}</b></div>
      <div class="confidence">${d.confidence}% confidence</div>
      <div class="sensor-box">
        🌡️ Temp: <b>${d.temp}°C</b> &nbsp;|&nbsp; 💧 Humidity: <b>${d.humidity}%</b><br>
        📏 Distance: <b>${d.distance} cm</b> &nbsp;|&nbsp; 📍 <b>${d.lat}, ${d.lon}</b>
      </div>
      <div class="btn-row">
        <button class="btn btn-confirm" onclick="confirm_()">✔ Confirm</button>
        <button class="btn btn-reject" onclick="reject()">✖ Reject</button>
      </div>`;
  }
};
function confirm_() {
  fetch('/confirm', {method:'POST'}).then(r=>r.json()).then(d=>{ if(d.success) alert('Saved!'); });
}
function reject() {
  fetch('/reject', {method:'POST'});
}
</script>
</body>
</html>"""


@app.route('/review')
def review_page():
    return render_template_string(REVIEW_HTML)


@app.route('/stream')
def stream():
    def generate():
        yield f"data: {json.dumps(current_review)}\n\n"
        while True:
            try:
                update = review_queue.get(timeout=30)
                yield f"data: {json.dumps(update)}\n\n"
            except queue.Empty:
                yield "data: {}\n\n"
    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route('/confirm', methods=['POST'])
def confirm():
    global current_review
    if current_review.get('state') != 'result':
        return jsonify({"success": False, "error": "No result to confirm"})
    record = {
        "species": current_review.get("species"),
        "alert": current_review.get("alert"),
        "status": current_review.get("status"),
        "habitat_match": current_review.get("habitat_match"),
        "venomous": current_review.get("venomous"),
        "approachability": current_review.get("approachability"),
        "confidence": current_review.get("confidence"),
        "lat": current_review.get("lat"),
        "lon": current_review.get("lon"),
        "temp": current_review.get("temp"),
        "humidity": current_review.get("humidity"),
        "distance": current_review.get("distance"),
        "timestamp": current_review.get("timestamp"),
        "image_url": current_review.get("image_url", ""),
    }
    supabase_client.table("observations").insert(record).execute()
    return jsonify({"success": True})


@app.route('/reject', methods=['POST'])
def reject():
    global current_review
    current_review = {"state": "awaiting"}
    review_queue.put(current_review.copy())
    return jsonify({"success": True})


if __name__ == '__main__':
    print("FieldLog Backend Active on http://localhost:8000")
    app.run(debug=True, port=8000, host='0.0.0.0', threaded=True)
