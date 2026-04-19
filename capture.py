import cv2
import numpy as np
import requests
import time

SENSITIVITY = 8000
SETTLE_DELAY = 1.5
COOLDOWN = 15
UPLOAD_URL = "http://localhost:8000/upload_capture"
LAT = "32.8800"
LON = "-117.2350"
CAPTURE_PATH = "/tmp/capture.jpg"


def open_camera():
    for index in [0, 1, 2]:
        cap = cv2.VideoCapture(index)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            return cap
    raise RuntimeError("No webcam found at index 0, 1, or 2")


def to_gray_blur(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (21, 21), 0)


def send_capture(path):
    with open(path, "rb") as f:
        response = requests.post(
            UPLOAD_URL,
            files={"image": ("capture.jpg", f, "image/jpeg")},
            data={"lat": LAT, "lon": LON},
            timeout=10,
        )
    return response


def main():
    cap = open_camera()
    ret, prev_frame = cap.read()
    if not ret:
        raise RuntimeError("Failed to read initial frame from webcam")

    prev_gray = to_gray_blur(prev_frame)

    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue

        gray = to_gray_blur(frame)
        diff = cv2.absdiff(prev_gray, gray)
        _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        non_zero = cv2.countNonZero(thresh)

        if non_zero > SENSITIVITY:
            print("Motion detected")
            time.sleep(SETTLE_DELAY)

            print("Capturing...")
            ret, capture_frame = cap.read()
            if ret:
                cv2.imwrite(CAPTURE_PATH, capture_frame)

                try:
                    response = send_capture(CAPTURE_PATH)
                    print("Sent to backend")
                    print(response.text)
                except Exception as e:
                    print(f"POST failed: {e}")

            time.sleep(COOLDOWN)

            ret, frame = cap.read()
            if ret:
                gray = to_gray_blur(frame)

        prev_gray = gray
        time.sleep(0.05)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped.")
