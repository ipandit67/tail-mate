import cv2
import io
import os
import random
import pandas as pd
import json
import queue
import threading
import numpy as np
import requests as http_requests
from flask import Flask, request, jsonify, render_template_string, Response, stream_with_context
from flask_cors import CORS
from datetime import datetime
import google.generativeai as genai
import PIL.Image  # For handling the photo
from PIL import Image
from supabase import create_client


app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# --- TEST MODE ---
TEST_MODE = True
TEST_SPECIES = ["Western Fence Lizard", "Southern Pacific Rattlesnake", "Southern Alligator Lizard", "Orange-throated Whiptail"]

# --- ARDUINO DEVICE IPs ---
DEVICE1_IP = "tailmate.local"
DEVICE2_IP = "tailmate2.local"


@app.route('/')
@app.route('/index.html')
def serve_index():
    return app.send_static_file('index.html')

# --- SUPABASE CONFIGURATION ---
SUPABASE_URL = "https://kazkfrgbnsatagfckjpa.supabase.co"
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImthemtmcmdibnNhdGFnZmNranBhIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NjU0Nzg3NiwiZXhwIjoyMDkyMTIzODc2fQ.Z40rSTsoQufUqM81ICBCxEIGab9cL88sS8t1eRrO8W8"
supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# --- REVIEW STATE (display only — no confirmation flow) ---
review_queue = queue.Queue()
current_review = {"state": "awaiting"}

# --- SESSION EVENTS (in-memory, for /events endpoint) ---
session_events = []

# --- LATEST SENSOR READINGS ---
latest_sensors = {"temp": 0, "humidity": 0, "distance": 0, "movement": 0}
latest_sensor_timestamp = None

# --- GEMINI CONFIGURATION ---
genai.configure(api_key="AIzaSyAdlzNMwNigeVmeheBwF1E1jrUmQpTfiRY")
model = genai.GenerativeModel('gemini-1.5-flash')


# ==========================================
# BACKGROUND: ARDUINO SENSOR POLLING
# ==========================================
def poll_sensors():
    global latest_sensors, latest_sensor_timestamp
    while True:
        import time
        for ip, label in [(DEVICE1_IP, "Device1"), (DEVICE2_IP, "Device2")]:
            try:
                resp = http_requests.get(f"http://{ip}:8000/sensors", timeout=3)
                data = resp.json()
                latest_sensors.update({k: v for k, v in data.items() if k in latest_sensors})
                latest_sensor_timestamp = datetime.now().isoformat()
            except Exception as e:
                print(f"Sensor poll failed ({label}): {e}")
        time.sleep(5)


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
# RELOCATION GUIDANCE DATABASE
# ==========================================
RELOCATION_GUIDANCE = {
    "Western Fence Lizard": {
        "safe_to_handle": True,
        "handling_method": "Gentle towel method — drape a soft cloth over the lizard, scoop carefully without squeezing the abdomen.",
        "relocation_steps": [
            "Approach calmly from the side, avoiding shadows over the animal.",
            "Drape a light towel over the lizard to reduce stress.",
            "Cup hands beneath the body, supporting the torso.",
            "Walk to nearest rocky, sunny area within 500m of capture point.",
            "Place lizard on a warm rock surface and step back quietly."
        ],
        "contact_agencies": [
            {"name": "San Diego Wildlife Services (if injured)", "phone": "619-225-9202"}
        ],
        "do_not": [
            "Do not relocate more than 500m from capture point.",
            "Do not handle by the tail — autotomy (tail drop) will occur."
        ]
    },
    "Southern Pacific Rattlesnake": {
        "safe_to_handle": False,
        "handling_method": "DO NOT HANDLE. Maintain a minimum 2-meter distance at all times.",
        "relocation_steps": [
            "Clear all people and pets from the immediate area (3m radius).",
            "Mark exact GPS location using your phone or GPS device.",
            "Photograph the snake from a safe distance for identification.",
            "Call San Diego County Vector Control: 858-694-2888.",
            "If injured, call CA Dept Fish & Wildlife: 858-467-4201.",
            "Wait for trained professional response — do not leave area unattended if children/pets nearby."
        ],
        "contact_agencies": [
            {"name": "San Diego County Vector Control", "phone": "858-694-2888"},
            {"name": "CA Dept Fish & Wildlife (injured animal)", "phone": "858-467-4201"}
        ],
        "do_not": [
            "Do not attempt capture under any circumstances.",
            "Do not corner the animal — provide a clear escape path.",
            "Do not approach within 2 meters (strike range).",
            "Do not handle even if appears dead — reflexive bites occur post-mortem."
        ]
    },
    "Southern Alligator Lizard": {
        "safe_to_handle": True,
        "handling_method": "Use thick leather gloves — these lizards bite hard and may draw blood.",
        "relocation_steps": [
            "Put on thick leather or rose-pruning gloves.",
            "Approach slowly from behind to avoid triggering defensive bite.",
            "Grip firmly behind the head and support the body — never the tail.",
            "Relocate to dense shrubby vegetation near a moisture source (creek, drainage).",
            "Release at ground level under cover of vegetation."
        ],
        "contact_agencies": [
            {"name": "San Diego Herpetological Society (if injured)", "phone": "858-715-9510"}
        ],
        "do_not": [
            "Do not handle the tail — autotomy risk and tail will not regenerate fully.",
            "Do not handle without thick gloves — bite force can puncture skin.",
            "Do not relocate to dry open areas — they require moisture."
        ]
    },
    "Orange-throated Whiptail": {
        "safe_to_handle": False,
        "handling_method": "VULNERABLE SPECIES — minimize handling. Use a soft catch bag only if absolutely necessary.",
        "relocation_steps": [
            "Document encounter with photographs and exact GPS coordinates.",
            "Note substrate, vegetation, and time of day.",
            "Do NOT relocate — observe and report only.",
            "Submit observation to iNaturalist immediately.",
            "Notify CA Dept Fish & Wildlife if outside documented range.",
            "If handling is medically necessary, use a soft cotton catch bag only."
        ],
        "contact_agencies": [
            {"name": "CA Dept Fish & Wildlife", "phone": "858-467-4201"},
            {"name": "iNaturalist", "phone": "https://inaturalist.org"}
        ],
        "do_not": [
            "Do not relocate — protected species under CA Fish & Game Code.",
            "Do not handle without permit unless animal is in immediate danger.",
            "Do not disturb surrounding habitat."
        ]
    },
    "Western Skink": {
        "safe_to_handle": True,
        "handling_method": "Handle gently — these animals are small and easily injured. Cup hands loosely.",
        "relocation_steps": [
            "Cup hands loosely around the skink, supporting the full body.",
            "Walk to nearest moist soil area near logs or rocks.",
            "Place gently on the substrate and allow self-release.",
            "Photograph location for documentation."
        ],
        "contact_agencies": [
            {"name": "San Diego Natural History Museum (unusual location)", "phone": "619-232-3821"}
        ],
        "do_not": [
            "Do not handle the tail — bright blue tail readily detaches.",
            "Do not relocate to dry open ground."
        ]
    },
    "California King Snake": {
        "safe_to_handle": True,
        "handling_method": "Use a snake hook or pillow case method. Non-venomous but may musk or bite defensively.",
        "relocation_steps": [
            "Approach with snake hook from the side.",
            "Lift gently mid-body and guide into a pillow case or snake bag.",
            "Tie the bag loosely and transport to release site.",
            "Release into nearest riparian or brushy habitat.",
            "Open bag and step back — allow snake to exit on its own time."
        ],
        "contact_agencies": [
            {"name": "CA Dept Fish & Wildlife (out of range)", "phone": "858-467-4201"}
        ],
        "do_not": [
            "Do not grab by the tail — risk of spinal injury.",
            "Do not relocate to highly developed areas."
        ]
    },
    "Western Side-blotched Lizard": {
        "safe_to_handle": True,
        "handling_method": "Cup hands gently — these are small, fast lizards that startle easily.",
        "relocation_steps": [
            "Approach slowly to minimize stress response.",
            "Cup loosely with both hands.",
            "Relocate to open sandy ground with scattered rocks.",
            "Release on warm substrate and step back."
        ],
        "contact_agencies": [
            {"name": "San Diego Wildlife Services (if injured)", "phone": "619-225-9202"}
        ],
        "do_not": [
            "Do not handle the tail.",
            "Low priority for agency contact unless visibly injured."
        ]
    },
    "San Diegan Legless Lizard": {
        "safe_to_handle": False,
        "handling_method": "VULNERABLE — DO NOT HANDLE without permit and training. Extremely fragile body structure.",
        "relocation_steps": [
            "Do NOT touch or move the animal.",
            "Document exact GPS location to within 1 meter accuracy.",
            "Photograph the substrate (sand composition, vegetation, moisture).",
            "Photograph the animal without flash if possible.",
            "Contact CDFW and SD Natural History Museum immediately.",
            "Stay nearby (5m+) to deter accidental disturbance until professionals arrive."
        ],
        "contact_agencies": [
            {"name": "CA Dept Fish & Wildlife", "phone": "858-467-4201"},
            {"name": "San Diego Natural History Museum", "phone": "619-232-3821"}
        ],
        "do_not": [
            "Do not handle without specialized training — body is extremely delicate.",
            "Do not disturb the surrounding soil or vegetation.",
            "Do not use flash photography at close range."
        ]
    },
    "_unknown": {
        "safe_to_handle": False,
        "handling_method": "DO NOT HANDLE unknown species. Misidentification can be fatal if animal is venomous.",
        "relocation_steps": [
            "Maintain a safe distance (minimum 3 meters).",
            "Photograph from multiple angles for identification.",
            "Submit photo and GPS to iNaturalist for species ID.",
            "Contact San Diego Natural History Museum for confirmation.",
            "Do not return to the area until species is identified."
        ],
        "contact_agencies": [
            {"name": "San Diego Natural History Museum", "phone": "619-232-3821"},
            {"name": "iNaturalist", "phone": "https://inaturalist.org"}
        ],
        "do_not": [
            "Do not handle until species is positively identified.",
            "Do not assume non-venomous status."
        ]
    }
}


def get_relocation_guidance(species_name):
    if species_name in RELOCATION_GUIDANCE:
        return RELOCATION_GUIDANCE[species_name]
    name_lower = species_name.lower()
    for key in RELOCATION_GUIDANCE:
        if key == "_unknown":
            continue
        if key.lower() in name_lower or name_lower in key.lower():
            return RELOCATION_GUIDANCE[key]
    return RELOCATION_GUIDANCE["_unknown"]


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


# ==========================================
# ANIMAL HEALTH ASSESSMENT ENGINE
# ==========================================
def assess_animal_health(species, temp, humidity, distance, in_habitat, is_venomous):
    flags = []
    severity = "green"  # green | orange | red

    def bump(level):
        nonlocal severity
        order = {"green": 0, "orange": 1, "red": 2}
        if order[level] > order[severity]:
            severity = level

    # Temperature rules
    if temp > 38:
        flags.append({"icon": "🔥", "level": "red", "text": "Critical heat stress — reptile body temperature dangerously elevated"})
        bump("red")
    elif temp > 32:
        flags.append({"icon": "🌡️", "level": "orange", "text": "Elevated thermal stress — reptile likely seeking shade"})
        bump("orange")
    elif temp < 10:
        flags.append({"icon": "❄️", "level": "orange", "text": "Hypothermic risk — reptile mobility severely reduced"})
        bump("orange")
    elif 18 <= temp <= 30:
        flags.append({"icon": "✓", "level": "green", "text": "Optimal thermal range"})

    # Humidity rules
    if humidity < 20:
        flags.append({"icon": "💧", "level": "red", "text": "Critically low humidity — dehydration risk high"})
        bump("red")
    elif humidity < 35:
        flags.append({"icon": "💧", "level": "orange", "text": "Low humidity — monitor for dehydration"})
        bump("orange")
    elif humidity > 85:
        flags.append({"icon": "💦", "level": "orange", "text": "High humidity — fungal infection risk for some species"})
        bump("orange")

    # Distance rules
    if distance < 20 and is_venomous:
        flags.append({"icon": "⚠️", "level": "red", "text": "CRITICAL: Dangerously close to venomous animal — back away immediately"})
        bump("red")
    elif distance < 50 and is_venomous:
        flags.append({"icon": "⚠️", "level": "orange", "text": "Warning: Within strike range of venomous species"})
        bump("orange")
    elif distance < 30 and not is_venomous:
        flags.append({"icon": "⚠️", "level": "orange", "text": "Very close encounter — animal may feel threatened"})
        bump("orange")

    # Habitat rules
    if not in_habitat:
        flags.append({"icon": "🗺️", "level": "orange", "text": "Species outside documented range — possible displacement or climate shift"})
        bump("orange")

    # Combined stress index
    if temp > 35 and humidity < 25:
        flags.append({"icon": "🔥", "level": "red", "text": "Combined heat and dehydration stress — animal in survival mode"})
        bump("red")
    if not in_habitat and (temp > 33 or temp < 12):
        flags.append({"icon": "🚨", "level": "red", "text": "Displaced animal in thermal stress — intervention may be needed"})
        bump("red")

    color_map = {"green": "#4A7C59", "orange": "#E8923A", "red": "#C0392B"}

    if severity == "red":
        recommendations = "Animal is in critical condition. Maintain safe distance and contact wildlife professionals immediately."
    elif severity == "orange":
        recommendations = "Animal is experiencing measurable stress. Observe carefully and avoid disturbing the animal further."
    else:
        recommendations = "Animal appears healthy and within optimal field conditions for the species."

    return {
        "health_status": severity.upper(),
        "health_color": color_map[severity],
        "health_flags": flags,
        "recommendations": recommendations,
    }


MOCK_NOTES = {
    "Western Fence Lizard": "Common diurnal lizard with blue belly patches. Often seen basking on rocks and fences across San Diego.",
    "Southern Pacific Rattlesnake": "Venomous pit viper with distinct rattle. Active at dawn/dusk in rocky chaparral habitats.",
    "Southern Alligator Lizard": "Long-bodied lizard with armored scales. Bites defensively — found in moist understory.",
    "Orange-throated Whiptail": "Vulnerable species with bright orange throat. Fast-moving, active mid-day in coastal sage scrub.",
}


def generate_species_notes(species_name):
    if TEST_MODE:
        return MOCK_NOTES.get(species_name, f"Field note placeholder for {species_name}.")
    try:
        prompt = f"Write a brief 2-3 sentence field note about {species_name} in San Diego. Focus on identifying features and behavior. Keep it under 150 characters."
        response = client.models.generate_content(model='gemini-2.0-flash', contents=[prompt])
        return response.text.strip()
    except Exception as e:
        # Compact error so it doesn't pollute the notes column with multi-KB blobs
        return f"Notes unavailable: {type(e).__name__}"


# ==========================================
# 3. API ENDPOINTS
# ==========================================

@app.route('/sensors', methods=['POST'])
def sensors():
    global latest_sensors, latest_sensor_timestamp
    latest_sensors = {
        "temp": float(request.form.get('temp', latest_sensors['temp'])),
        "humidity": float(request.form.get('humidity', latest_sensors['humidity'])),
        "distance": float(request.form.get('distance', latest_sensors['distance'])),
        "movement": float(request.form.get('movement', latest_sensors.get('movement', 0))),
        "movement_x": float(request.form.get('movement_x', latest_sensors.get('movement_x', 0))),
        "movement_y": float(request.form.get('movement_y', latest_sensors.get('movement_y', 0))),
        "movement_z": float(request.form.get('movement_z', latest_sensors.get('movement_z', 0))),
    }
    latest_sensor_timestamp = datetime.now().isoformat()
    return jsonify({"success": True})


@app.route('/observations', methods=['GET'])
def observations():
    result = supabase_client.table("observations").select("*").order("timestamp", desc=True).execute()
    return jsonify(result.data)


@app.route('/events', methods=['GET'])
def events():
    return jsonify(list(reversed(session_events)))


@app.route('/upload_capture', methods=['POST'])
def handle_arduino_trigger():
    global current_review, _motion_flash_until

    # Try to capture a fresh frame directly from the local camera
    cap = cv2.VideoCapture(0)
    for _ in range(5): cap.read()  # flush stale frames
    ret, frame = cap.read()
    cap.release()

    if ret:
        img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(img_rgb)
        is_success, buffer = cv2.imencode(".jpg", frame)
        img_bytes = buffer.tobytes()
    else:
        # Fallback to uploaded file if camera capture fails
        img_file = request.files.get('image')
        if img_file:
            img_bytes = img_file.read()
            img = PIL.Image.open(io.BytesIO(img_bytes))
        else:
            img_bytes = b''
            img = PIL.Image.new('RGB', (640, 480))

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

    if TEST_MODE:
        detected_species = random.choice(TEST_SPECIES)
        print(f"TEST MODE: skipping Gemini, using mock species: {detected_species}")
    else:
        prompt = "Identify this San Diego reptile. Give me ONLY the common name."
        response = model.generate_content([prompt, img])
        detected_species = response.text.strip()
    lat = float(request.form.get('lat', 32.880))
    lon = float(request.form.get('lon', -117.235))

    alert_color, status_text, habitat_ok = analyze_capture(detected_species, lat, lon)

    is_venomous = detected_species in VENOMOUS_SPECIES
    approachability = get_approachability(is_venomous, alert_color, habitat_ok, status_text)
    approachability_color = "#4A7C59" if alert_color == "GREEN" else ("#E8923A" if status_text == "Common" else "#C0392B")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    notes = generate_species_notes(detected_species)

    # Health assessment
    health = assess_animal_health(detected_species, temp, humidity, distance, habitat_ok, is_venomous)

    # Relocation guidance
    relocation = get_relocation_guidance(detected_species)
    relocation_needed = (not habitat_ok) or (health["health_status"] in ("RED", "ORANGE")) or is_venomous

    # Only include columns that exist in the observations table
    record = {
        "species_name": detected_species,
        "alert_color": alert_color,
        "conservation_status": status_text,
        "habitat_match": habitat_ok,
        "venomous": is_venomous,
        "approachability": approachability,
        "confidence": 92,
        "latitude": lat,
        "longitude": lon,
        "temperature": temp,
        "humidity": humidity,
        "distance_cm": distance,
        "timestamp": timestamp,
        "notes": notes,
    }

    saved_ok = False
    saved_id = None
    try:
        result = supabase_client.table("observations").insert(record).execute()
        if result.data:
            saved_ok = True
            saved_id = result.data[0].get("id")
            print(f"Supabase insert OK: id={saved_id}, species={detected_species}")
        else:
            print(f"Supabase insert returned no data: {result}")
    except Exception as e:
        print(f"Supabase insert FAILED: {type(e).__name__}: {e}")

    # Append to session events only after confirmed save
    if saved_ok:
        session_events.append({
            "id": saved_id,
            "species": detected_species,
            "timestamp": timestamp,
            "approachability": approachability,
            "confidence": 92,
            "image_url": image_url,
            "health_status": health["health_status"],
            "health_flags": health["health_flags"],
            "lat": lat,
            "lon": lon,
            "venomous": is_venomous,
            "status": status_text,
            "habitat_match": habitat_ok,
        })

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
        "health_status": health["health_status"],
        "health_color": health["health_color"],
        "health_flags": health["health_flags"],
        "recommendations": health["recommendations"],
        "relocation": relocation,
        "relocation_needed": relocation_needed,
        "saved": saved_ok,
        "sea_temp": 19.2,
    }
    review_queue.put(current_review.copy())

    return jsonify({
        "species": detected_species,
        "alert": alert_color,
        "status": status_text,
        "saved": saved_ok,
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
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cam = cap
                break

        if cam is None:
            return

        # Flush stale/black initialization frames from the camera buffer
        for _ in range(10):
            cam.read()
            time.sleep(0.05)

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


# ==========================================
# REVIEW PAGE — full field intelligence briefing (display only)
# ==========================================
REVIEW_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>TailMate Field Briefing</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700&family=Roboto+Condensed:wght@400;700&display=swap" rel="stylesheet"/>
<style>
  *{box-sizing:border-box;margin:0;padding:0;}
  html,body{background:#F5F0E8;font-family:'Roboto Condensed',sans-serif;color:#2C3E2D;min-height:100vh;}
  .topbar{background:#2C3E2D;color:#F5F0E8;padding:14px 20px;display:flex;justify-content:space-between;align-items:center;font-size:14px;letter-spacing:1px;}
  .topbar .logo{font-family:'Playfair Display',serif;font-size:22px;}
  .topbar .meta{font-size:12px;color:#bbb;text-align:right;line-height:1.5;}
  #app{padding:20px 16px 40px;max-width:560px;margin:0 auto;}
  .awaiting{min-height:80vh;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;}
  .emoji{font-size:80px;margin-bottom:16px;}
  @keyframes pulse{0%,100%{opacity:1;}50%{opacity:0.4;}}
  .pulse{animation:pulse 2s infinite;}
  .spinner{width:48px;height:48px;border:5px solid #ddd;border-top-color:#4A6B8A;border-radius:50%;animation:spin 0.9s linear infinite;margin:0 auto 20px;}
  @keyframes spin{to{transform:rotate(360deg);}}
  .card{background:#fff;border-radius:14px;padding:20px;margin-bottom:14px;box-shadow:0 2px 12px rgba(0,0,0,0.06);}
  .card-label{font-size:11px;letter-spacing:2px;color:#888;text-transform:uppercase;margin-bottom:10px;font-weight:700;}
  .species-name{font-family:'Playfair Display',serif;font-size:38px;color:#2C3E2D;line-height:1.1;margin-bottom:6px;}
  .conf-line{font-size:14px;color:#4A6B8A;margin-bottom:14px;}
  .badge{display:inline-block;padding:10px 22px;border-radius:50px;font-size:18px;font-weight:700;color:#fff;margin-bottom:10px;}
  .venomous-warn{background:#C0392B;color:#fff;border-radius:8px;padding:10px 16px;font-size:16px;font-weight:700;margin:8px 0;text-align:center;letter-spacing:1px;}
  .info-row{font-size:14px;margin:6px 0;color:#2C3E2D;}
  .info-row b{color:#4A6B8A;}
  .health-pill{display:inline-block;padding:8px 18px;border-radius:30px;color:#fff;font-weight:700;font-size:15px;margin-bottom:12px;letter-spacing:1px;}
  .health-flag{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;background:#F5F0E8;border-radius:8px;margin-bottom:6px;font-size:13px;border-left:4px solid;}
  .flag-icon{font-size:18px;flex-shrink:0;}
  .flag-text{flex:1;line-height:1.4;}
  .recs{font-size:13px;color:#4A6B8A;margin-top:12px;font-style:italic;line-height:1.5;}
  .env-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;}
  .env-item{background:#F5F0E8;border-radius:8px;padding:10px 12px;}
  .env-item .lbl{font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;}
  .env-item .val{font-size:18px;font-weight:700;margin-top:2px;}
  .env-item.warn{border-left:4px solid #E8923A;}
  .env-item.danger{border-left:4px solid #C0392B;}
  .env-item.ok{border-left:4px solid #4A7C59;}
  .reloc-handle{display:flex;align-items:center;gap:10px;padding:12px;background:#F5F0E8;border-radius:8px;margin-bottom:10px;font-weight:700;}
  .reloc-handle.safe{border-left:5px solid #4A7C59;color:#4A7C59;}
  .reloc-handle.unsafe{border-left:5px solid #C0392B;color:#C0392B;}
  .reloc-method{font-size:13px;color:#2C3E2D;background:#F5F0E8;padding:10px 12px;border-radius:8px;margin-bottom:12px;line-height:1.5;}
  .reloc-steps{counter-reset:step;list-style:none;padding:0;margin:0 0 12px;}
  .reloc-steps li{counter-increment:step;padding:8px 0 8px 36px;position:relative;font-size:13px;line-height:1.45;border-bottom:1px solid #eee;}
  .reloc-steps li::before{content:counter(step);position:absolute;left:0;top:7px;background:#4A7C59;color:#fff;width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:12px;}
  .reloc-steps li:last-child{border-bottom:none;}
  .reloc-agencies{margin-bottom:12px;}
  .agency{display:block;padding:10px 12px;background:#4A6B8A;color:#fff;border-radius:8px;text-decoration:none;margin-bottom:6px;font-size:14px;font-weight:700;}
  .agency:hover{background:#3a5571;}
  .donot-list{background:#FCEAE7;border:1px solid #C0392B;border-radius:8px;padding:10px 12px;}
  .donot-list .donot-title{color:#C0392B;font-weight:700;font-size:12px;letter-spacing:2px;margin-bottom:6px;}
  .donot-list ul{list-style:none;padding:0;}
  .donot-list li{font-size:13px;color:#A8281C;padding:4px 0 4px 16px;position:relative;}
  .donot-list li::before{content:'✕';position:absolute;left:0;color:#C0392B;font-weight:700;}
  .saved-indicator{text-align:center;padding:12px;color:#4A7C59;font-weight:700;font-size:14px;background:#E8F2EB;border-radius:8px;margin-top:12px;}
  .saved-indicator.fail{color:#C0392B;background:#FCEAE7;}
</style>
</head>
<body>
<div class="topbar">
  <div class="logo">🐍 TailMate</div>
  <div class="meta" id="topbar-meta">—</div>
</div>
<div id="app">
  <div class="awaiting"><div class="emoji pulse">🐍</div><div style="font-family:'Playfair Display',serif;font-size:24px;">Awaiting capture…</div></div>
</div>
<script>
const app = document.getElementById('app');
const topbarMeta = document.getElementById('topbar-meta');
const es = new EventSource('/stream');

function envClass(metric, value) {
  if (metric === 'temp') {
    if (value > 38 || value < 10) return 'danger';
    if (value > 32) return 'warn';
    return 'ok';
  }
  if (metric === 'humidity') {
    if (value < 20) return 'danger';
    if (value < 35 || value > 85) return 'warn';
    return 'ok';
  }
  if (metric === 'distance') {
    if (value < 20) return 'danger';
    if (value < 50) return 'warn';
    return 'ok';
  }
  return '';
}

function renderResult(d) {
  topbarMeta.innerHTML = `${d.timestamp}<br>${d.lat.toFixed(4)}, ${d.lon.toFixed(4)}`;

  const venomHtml = d.venomous ? `<div class="venomous-warn">⚠ VENOMOUS SPECIES</div>` : '';

  const flagsHtml = (d.health_flags || []).map(f => {
    const colorMap = {green:'#4A7C59', orange:'#E8923A', red:'#C0392B'};
    return `<div class="health-flag" style="border-color:${colorMap[f.level]};">
      <div class="flag-icon">${f.icon}</div>
      <div class="flag-text">${f.text}</div>
    </div>`;
  }).join('');

  const tempCls = envClass('temp', d.temp);
  const humCls = envClass('humidity', d.humidity);
  const distCls = envClass('distance', d.distance);
  const proxNote = (d.distance < 50 && d.venomous) ? '<div style="color:#C0392B;font-size:11px;margin-top:4px;font-weight:700;">⚠ STRIKE RANGE</div>' : '';

  const showReloc = (!d.habitat_match) || (d.health_status === 'RED' || d.health_status === 'ORANGE') || d.venomous;
  const r = d.relocation || {};
  let relocHtml = '';
  if (showReloc && r.handling_method) {
    const safeCls = r.safe_to_handle ? 'safe' : 'unsafe';
    const safeIcon = r.safe_to_handle ? '✓ SAFE TO HANDLE' : '✕ DO NOT HANDLE';
    const stepsHtml = (r.relocation_steps || []).map(s => `<li>${s}</li>`).join('');
    const agenciesHtml = (r.contact_agencies || []).map(a => {
      const phoneHref = a.phone.startsWith('http') ? a.phone : `tel:${a.phone.replace(/[^0-9+]/g, '')}`;
      return `<a class="agency" href="${phoneHref}">📞 ${a.name}<br><span style="font-weight:400;font-size:13px;">${a.phone}</span></a>`;
    }).join('');
    const donotHtml = (r.do_not || []).map(x => `<li>${x}</li>`).join('');
    relocHtml = `
      <div class="card">
        <div class="card-label">Relocation Guidance</div>
        <div class="reloc-handle ${safeCls}">${safeIcon}</div>
        <div class="reloc-method">${r.handling_method}</div>
        <div class="card-label" style="margin-top:14px;">Steps</div>
        <ol class="reloc-steps">${stepsHtml}</ol>
        <div class="card-label">Contact Agencies</div>
        <div class="reloc-agencies">${agenciesHtml}</div>
        <div class="donot-list">
          <div class="donot-title">DO NOT</div>
          <ul>${donotHtml}</ul>
        </div>
      </div>`;
  }

  const savedHtml = d.saved
    ? `<div class="saved-indicator">Saved to field log ✓</div>`
    : `<div class="saved-indicator fail">Save failed — check connection</div>`;

  app.innerHTML = `
    <div class="card">
      <div class="card-label">Species Identification</div>
      <div class="species-name">${d.species}</div>
      <div class="conf-line">${d.confidence}% AI confidence</div>
      <div class="badge" style="background:${d.approachability_color}">${d.approachability}</div>
      ${venomHtml}
      <div class="info-row">📋 Conservation: <b>${d.status}</b></div>
      <div class="info-row">🗺️ Habitat: <b>${d.habitat_match ? 'In documented range ✓' : 'Outside documented range ⚠'}</b></div>
    </div>

    <div class="card">
      <div class="card-label">Animal Health Assessment</div>
      <div class="health-pill" style="background:${d.health_color}">${d.health_status}</div>
      ${flagsHtml}
      <div class="recs">${d.recommendations || ''}</div>
    </div>

    <div class="card">
      <div class="card-label">Environmental Conditions</div>
      <div class="env-grid">
        <div class="env-item ${tempCls}"><div class="lbl">Temp</div><div class="val">${d.temp}°C</div></div>
        <div class="env-item ${humCls}"><div class="lbl">Humidity</div><div class="val">${d.humidity}%</div></div>
        <div class="env-item ${distCls}"><div class="lbl">Distance</div><div class="val">${d.distance} cm</div>${proxNote}</div>
        <div class="env-item ok"><div class="lbl">Scripps SST</div><div class="val">${d.sea_temp || 19.2}°C</div></div>
      </div>
    </div>

    ${relocHtml}

    ${savedHtml}
  `;
}

es.onmessage = function(e) {
  const d = JSON.parse(e.data);
  if (!d.state) return;
  if(d.state === 'awaiting') {
    topbarMeta.innerHTML = '—';
    app.innerHTML = `<div class="awaiting"><div class="emoji pulse">🐍</div><div style="font-family:'Playfair Display',serif;font-size:24px;">Awaiting capture…</div></div>`;
  } else if(d.state === 'processing') {
    topbarMeta.innerHTML = 'Processing…';
    app.innerHTML = `
      <div class="awaiting">
        <div class="spinner"></div>
        <div style="font-size:18px;color:#4A6B8A;margin-bottom:14px;">Identifying species…</div>
        <div style="background:#fff;border-radius:12px;padding:16px 24px;font-size:14px;color:#4A6B8A;line-height:2;">
          🌡️ Temp: <b>${d.temp}°C</b><br>
          💧 Humidity: <b>${d.humidity}%</b><br>
          📏 Distance: <b>${d.distance} cm</b>
        </div>
      </div>`;
  } else if(d.state === 'result') {
    renderResult(d);
  }
};
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


@app.route('/status', methods=['GET'])
def status():
    PLACEHOLDER_KEY = "YOUR_API_KEY_HERE"
    api_key = "AIzaSyAdlzNMwNigeVmeheBwF1E1jrUmQpTfiRY"
    gemini_ok = bool(api_key and api_key != PLACEHOLDER_KEY)

    supabase_ok = False
    try:
        supabase_client.table("observations").select("id").limit(1).execute()
        supabase_ok = True
    except Exception:
        pass

    return jsonify({
        "test_mode": TEST_MODE,
        "gemini_key_present": gemini_ok,
        "backend_running": True,
        "last_sensor_time": latest_sensor_timestamp,
        "latest_sensors": latest_sensors,
        "session_events_count": len(session_events),
        "supabase_connected": supabase_ok,
    })


threading.Thread(target=poll_sensors, daemon=True).start()

if __name__ == '__main__':
    print("FieldLog Backend Active on http://localhost:8000")
    app.run(debug=True, port=8000, host='0.0.0.0', threaded=True)
