import os
import base64
import re
from datetime import datetime

import cv2
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from gtts import gTTS
import pyttsx3
from openai import OpenAI

from db_config import get_db_connection

# -------------------- ENV --------------------
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CAMERA_URL = os.getenv("CAMERA_URL")

# -------------------- APP CONFIG --------------------
app = Flask(__name__)
app.secret_key = "supersecretkey"

UPLOAD_FOLDER = "static/uploads"
AUDIO_FOLDER = "static/audio"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(AUDIO_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["AUDIO_FOLDER"] = AUDIO_FOLDER

# -------------------- OPENAI --------------------
client = OpenAI(api_key=OPENAI_API_KEY)

# -------------------- TEXT TO SPEECH --------------------
engine = pyttsx3.init()
engine.setProperty("rate", 150)

# -------------------- FUNCTIONS --------------------
def extract_plate_number(image_path):
    with open(image_path, "rb") as img:
        image_base64 = base64.b64encode(img.read()).decode("utf-8")

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Extract only the vehicle number from this image."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
            ]
        }],
        max_tokens=50
    )

    text = response.choices[0].message.content.upper().replace(" ", "")
    match = re.search(r"[A-Z]{2}\d{1,2}[A-Z]{1,2}\d{3,4}", text)
    return match.group(0) if match else "UNKNOWN"


def announce_desktop(truck_number):
    engine.say(f"Truck number {truck_number} please come inside the gate.")
    engine.runAndWait()


def generate_browser_audio(truck_number):
    message = (
        f"Truck number {truck_number} please come inside the gate. "
        f"Truck number {truck_number} कृपया गेट के अंदर आइए।"
    )
    filename = f"{truck_number}_announcement.mp3"
    path = os.path.join(app.config["AUDIO_FOLDER"], filename)
    gTTS(text=message, lang="hi").save(path)
    return filename

# -------------------- ROUTES --------------------
@app.route("/capture")
def capture_plate():
    cap = cv2.VideoCapture(CAMERA_URL)
    ret, frame = cap.read()
    cap.release()

    if not ret:
        flash("Camera capture failed")
        return redirect(url_for("upload"))

    filename = f"plate_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    cv2.imwrite(path, frame)

    return redirect(url_for("upload", captured=filename))


@app.route("/", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        plate_file = request.form.get("captured_plate_filename")
        license_img = request.files.get("license")
        challan = request.files.get("challan")

        if not all([plate_file, license_img, challan]):
            flash("All fields required")
            return redirect(url_for("upload"))

        plate_path = os.path.join(app.config["UPLOAD_FOLDER"], plate_file)
        license_path = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(license_img.filename))
        challan_path = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(challan.filename))

        license_img.save(license_path)
        challan.save(challan_path)

        truck_number = extract_plate_number(plate_path)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO trucks (truck_number, license_path, challan_path, plate_path, status)
            VALUES (%s,%s,%s,%s,%s)
        """, (truck_number, license_path, challan_path, plate_path, "Queued"))
        conn.commit()
        cur.close()
        conn.close()

        flash(f"Truck {truck_number} registered")
        return redirect(url_for("upload"))

    return render_template("upload.html", captured=request.args.get("captured"))


@app.route("/gate", methods=["GET", "POST"])
def gate():
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == "POST":
        cur.execute("SELECT id, truck_number FROM trucks WHERE status='Queued' ORDER BY id LIMIT 1")
        truck = cur.fetchone()

        if truck:
            cur.execute("UPDATE trucks SET status='Entered' WHERE id=%s", (truck[0],))
            conn.commit()
            announce_desktop(truck[1])
            audio = generate_browser_audio(truck[1])
            flash(f"Truck {truck[1]} announced")
            return redirect(url_for("gate", audio=audio))

    cur.execute("SELECT id, truck_number, status FROM trucks WHERE status='Queued'")
    queue = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("gate_entry.html", queue=queue, audio=request.args.get("audio"))


@app.route("/static/audio/<filename>")
def play_audio(filename):
    return send_from_directory(app.config["AUDIO_FOLDER"], filename)


if __name__ == "__main__":
    app.run(debug=True)
