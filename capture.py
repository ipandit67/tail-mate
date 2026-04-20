import cv2
import requests
import time

INTERVAL = 60
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

    while True:
        print("Auto-capture triggered")

        ret, frame = cap.read()
        if ret:
            cv2.imwrite(CAPTURE_PATH, frame)
            try:
                response = send_capture(CAPTURE_PATH)
                print("Sent to backend")
                print(response.text)
            except Exception as e:
                print(f"POST failed: {e}")
        else:
            print("Failed to read frame from webcam")

        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped.")
