import pandas as pd
import json
from flask import Flask, request, jsonify
from datetime import datetime
import google.generativeai as genai
import PIL.Image  # For handling the photo


app = Flask(__name__)
# --- GEMINI CONFIGURATION ---
genai.configure(api_key="YOUR_GEMINI_API_KEY")
model = genai.GenerativeModel('gemini-1.5-flash')

@app.route('/upload_capture', methods=['POST'])
def handle_arduino_trigger():
    # 1. Get the image from the Arduino/Laptop Camera
    img_file = request.files['image']
    img = PIL.Image.open(img_file)

    # 2. ASK THE BRAIN (The Gemini API call)
    prompt = "Identify this San Diego reptile. Give me ONLY the common name."
    response = model.generate_content([prompt, img])
    detected_species = response.text.strip() # Example: "Coast Horned Lizard"

    # 3. USE YOUR DATA (The Dictionary check you already have)
    # This checks the Gemini result against your observations-712033.csv
    alert_color, status_text, habitat_ok = analyze_capture(detected_species, lat, lon)

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

# ==========================================
# 3. API ENDPOINTS (Steps 2, 3, 6)
# ==========================================
@app.route('/upload_capture', methods=['POST'])
def handle_arduino_trigger():
    """
    Step 2: Endpoint that receives the still frame and coordinates.
    """
    # In a real demo, you'd use request.files['image'] 
    # For now, we simulate the species identification
    detected_species = request.form.get('detected_species', 'Western Fence Lizard')
    lat = float(request.form.get('lat', 32.880))
    lon = float(request.form.get('lon', -117.235))

    # Run Logic
    alert_color, status_text, habitat_ok = analyze_capture(detected_species, lat, lon)

    # Step 5: Environmental Data (Scripps Challenge)
    # Mocking a pull from Scripps Pier data
    env_conditions = {"temp_c": 19.2, "location": "Scripps Pier"}

    # Step 6: Structured JSON Note for Frontend
    log_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "species": detected_species,
        "status": status_text,
        "habitat_match": "Yes" if habitat_ok else "No (Outside Range)",
        "coordinates": f"{lat}, {lon}",
        "environment": env_conditions,
        "alert": alert_color,
        "image_url": "link_to_saved_image.jpg"
    }

    # Return this to the Arduino (Step 3) and save for Dashboard (Step 4)
    return jsonify(log_entry)

if __name__ == '__main__':
    print("FieldLog Backend Active on http://localhost:5000")
    app.run(debug=True, port=5000)