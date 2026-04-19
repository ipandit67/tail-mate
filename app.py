import pandas as pd
import json
import queue
import threading
from flask import Flask, request, jsonify, render_template_string, Response, stream_with_context
from flask_cors import CORS
from datetime import datetime
import google.genai as genai
import PIL.Image  # For handling the photo
from supabase import create_client


app = Flask(__name__)
CORS(app)
# --- SUPABASE CONFIGURATION ---
SUPABASE_URL = "https://kazkfrgbnsatagfckjpa.supabase.co"
# INSERT SERVICE ROLE KEY HERE
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImthemtmcmdibnNhdGFnZmNranBhIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NjU0Nzg3NiwiZXhwIjoyMDkyMTIzODc2fQ.Z40rSTsoQufUqM81ICBCxEIGab9cL88sS8t1eRrO8W8"
supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# --- REVIEW QUEUE ---
review_queue = queue.Queue()
current_review = {"state": "awaiting"}

# --- GEMINI CONFIGURATION ---
client = genai.Client(api_key="AIzaSyDgZ5Ge4rtpnopI0QCqKRtgMb18uzUo12U")

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
    response = client.models.generate_content(model='gemini-1.5-flash', contents=[prompt, img])
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
# ==========================================
# 1. DATA LAYER (From your uploaded CSV)
# ==========================================
def build_reference_db(csv_path):
    df = pd.read_csv(csv_path)
    
    # Manual Conservation Status Mapping (Step 3)
    status_map = {
        "Coast Horned Lizard": "Threatened (CA)",
        "Orange-throated Whiptail": "Vulnerable",
        "Red Diamond Rattlesnake": "Habitat Alert / Concern",
        "Western Fence Lizard": "Common / Safe",
        "Southern Pacific Rattlesnake": "Common"
    }

    # Extract geographic bounds for Habitat Match (Step 4)
    ranges = df.groupby('common_name').agg({
        'latitude': ['min', 'max'],
        'longitude': ['min', 'max']
    })
    ranges.columns = ['lat_min', 'lat_max', 'lon_min', 'lon_max']
    
    # Combine into a lookup dictionary
    db = {}
    for species in ranges.index:
        db[species] = {
            "bounds": ranges.loc[species].to_dict(),
            "status": status_map.get(species, "Unknown")
        }
    return db

# Initialize the Database
SPECIES_DB = build_reference_db('observations-712033.csv')

# ==========================================
# 2. ML & LOGIC LAYER
# ==========================================
def analyze_capture(species_name, lat, lon):
    """Core logic to determine if Arduino should alert Red or Green"""
    metadata = SPECIES_DB.get(species_name)
    
    if not metadata:
        return "RED", "Unknown Species", False

    # Check Endangered Status
    is_endangered = any(word in metadata['status'] for word in ["Threatened", "Vulnerable", "Endangered"])
    
    # Check Habitat Match (Step 4)
    b = metadata['bounds']
    # We add a small buffer (0.01) to the CSV range to be fair to the user
    in_habitat = (b['lat_min'] - 0.01 <= lat <= b['lat_max'] + 0.01) and \
                 (b['lon_min'] - 0.01 <= lon <= b['lon_max'] + 0.01)

    # Determine Alert (Step 3)
    # RED if endangered OR if species is found outside its known San Diego range
    alert = "RED" if (is_endangered or not in_habitat) else "GREEN"
    
    return alert, metadata['status'], in_habitat


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
      <div class="badge" style="background:${d.approachability_color}">${d.alert === 'GREEN' ? '✅ Approachable' : '🚫 Stay Back'}</div>
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
        # Send current state immediately on connect
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
        "confidence": current_review.get("confidence"),
        "lat": current_review.get("lat"),
        "lon": current_review.get("lon"),
        "temp": current_review.get("temp"),
        "humidity": current_review.get("humidity"),
        "distance": current_review.get("distance"),
        "timestamp": current_review.get("timestamp"),
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
    app.run(debug=True, port=8000)