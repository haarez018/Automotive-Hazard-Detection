# FULL VERSION — sends FRAME:<base64> and hazard JSON to backend
import cv2
import os
import asyncio
import json
import numpy as np
import base64
from pathlib import Path
from websockets.exceptions import ConnectionClosed

# ultralytics import may be heavy — guard if unavailable
try:
    from ultralytics import YOLO
except Exception:
    YOLO = None

# robust import for logging helper
try:
    from backend.verify_db import log_hazard
except Exception:
    from verify_db import log_hazard

WS_URL = "ws://127.0.0.1:8000/ws/video"
ROOT_DIR = Path(__file__).resolve().parent.parent
VIDEO_PATH = str(ROOT_DIR / "test_drive.mp4")

print("Loading YOLO model...")
model = None
if YOLO:
    try:
        model = YOLO("yolov8n.pt")
        print("YOLO model loaded ✅")
    except Exception as e:
        print("YOLO model not loaded:", e)
else:
    print("ultralytics YOLO unavailable — continuing with pothole-only detection.")

MIN_CONFIDENCE = 0.5
HAZARD_TYPE_POTHOLE = "Pothole"
HAZARD_TYPE_RASH = "Rash Driving"
RASH_MOVEMENT_THRESHOLD = 30.0  # tuned threshold
FRAME_SKIP_COUNT = 3
JPEG_QUALITY = 60
ENCODE_PARAM = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
VEHICLE_CLASSES = [2, 3, 5, 7]

OBJECT_TRACKER = {}  # track_id -> {'x','y','frame','last_seen'}
DETECTED_FRAMES_HISTORY = set()  # to avoid logging duplicate frames many times

# Pothole detection using contour heuristics (returns boolean and boxes list)
def detect_potholes_and_boxes(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (7, 7), 0)

    # adaptive threshold emphasizing dark regions
    thresh = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY_INV,
        25, 10
    )

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    pothole_regions = 0
    boxes = []

    h, w = frame.shape[:2]
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 500:  # skip tiny
            continue
        x, y, cw, ch = cv2.boundingRect(cnt)
        aspect_ratio = cw / float(ch)
        # heuristics (area relative to frame)
        if 700 < area < (w * h * 0.2) and 0.3 < aspect_ratio < 3.5:
            pothole_regions += 1
            boxes.append((x, y, x + cw, y + ch))

    return (pothole_regions >= 1, boxes)  # be more permissive: >=1

def detect_rash_driving(tracker_state, detections, frame_idx):
    """
    detections = list of [x1,y1,x2,y2,track_id,cls]
    returns list of alerts: {'track_id','speed','box'}
    """
    alerts = []
    current_ids = set()

    for (x1, y1, x2, y2, tid, cls) in detections:
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        current_ids.add(tid)

        if tid in tracker_state:
            prev = tracker_state[tid]
            dx = cx - prev['x']
            dy = cy - prev['y']
            dt = max(1, frame_idx - prev['frame'])
            speed = ((dx ** 2 + dy ** 2) ** 0.5) / dt
            tracker_state[tid] = {'x': cx, 'y': cy, 'frame': frame_idx, 'last_seen': frame_idx}
            if speed > RASH_MOVEMENT_THRESHOLD:
                alerts.append({'track_id': tid, 'speed': int(speed), 'box': (int(x1), int(y1), int(x2), int(y2))})
        else:
            tracker_state[tid] = {'x': cx, 'y': cy, 'frame': frame_idx, 'last_seen': frame_idx}

    # cleanup stale
    to_del = [tid for tid, v in tracker_state.items() if v.get('last_seen', 0) < frame_idx - 50]
    for tid in to_del:
        del tracker_state[tid]

    return alerts

async def stream_video_and_detect():
    print("\nChecking video file...")
    if not os.path.exists(VIDEO_PATH):
        print(f"❌ ERROR: Video file not found: {VIDEO_PATH}")
        return

    cap = cv2.VideoCapture(VIDEO_PATH)
    if not cap.isOpened():
        print("❌ ERROR: Could not open video file.")
        return

    frame_count = 0
    try:
        import websockets
        async with websockets.connect(WS_URL, open_timeout=10, ping_interval=None) as websocket:
            print(f"✅ WS CLIENT connected to {WS_URL}")

            while True:
                ret, frame = cap.read()
                if not ret:
                    print("✅ Video finished. Closing WebSocket.")
                    try:
                        await websocket.close()
                    except Exception:
                        pass
                    break

                frame_count += 1
                if frame_count % FRAME_SKIP_COUNT != 0:
                    continue

                # YOLO tracking (if model available)
                current_detections = []
                if model is not None:
                    try:
                        results = model.track(frame, conf=MIN_CONFIDENCE, persist=True, verbose=False)
                    except Exception as e:
                        print("YOLO processing error:", e)
                        await asyncio.sleep(0.01)
                        results = None

                    if results and len(results) > 0 and getattr(results[0], "boxes", None) is not None:
                        boxes = results[0].boxes
                        if getattr(boxes, "data", None) is not None:
                            data = boxes.data.cpu().numpy()
                            ids = getattr(boxes, "id", None)
                            ids_arr = ids.cpu().numpy() if ids is not None else np.arange(len(data))
                            for box, track_id in zip(data, ids_arr):
                                x1, y1, x2, y2 = box[:4]
                                cls = box[-1]
                                current_detections.append([float(x1), float(y1), float(x2), float(y2), int(track_id), int(cls)])
                # pothole detection on full frame
                pothole_detected, pothole_boxes = detect_potholes_and_boxes(frame)

                # rash detection
                rash_alerts = detect_rash_driving(OBJECT_TRACKER, current_detections, frame_count)

                # annotate frame: draw detection boxes & pothole boxes & rash boxes
                annotated = frame.copy()
                # draw YOLO boxes (if present)
                for det in current_detections:
                    x1, y1, x2, y2, tid, cls = det
                    cv2.rectangle(annotated, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                    cv2.putText(annotated, f"ID:{tid}", (int(x1), int(y1) - 6),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

                # pothole boxes - orange
                for bx in pothole_boxes:
                    x1, y1, x2, y2 = bx
                    cv2.rectangle(annotated, (int(x1), int(y1)), (int(x2), int(y2)), (0, 165, 255), 3)
                    cv2.putText(annotated, "Pothole", (int(x1), int(y1) - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 165, 255), 2)

                # rash alerts - red boxes
                for a in rash_alerts:
                    x1, y1, x2, y2 = a['box']
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 3)
                    cv2.putText(annotated, f"Rash ({a['speed']})", (x1, y1 - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

                # encode to JPEG
                success, buffer = cv2.imencode(".jpg", annotated, ENCODE_PARAM)
                if not success:
                    print("❌ JPEG encoding failed")
                    continue

                frame_bytes = buffer.tobytes()
                # send frame as base64 with prefix so frontend distinguishes
                try:
                    b64 = base64.b64encode(frame_bytes).decode('utf-8')
                    msg = "FRAME:" + b64
                    await websocket.send(msg)
                except ConnectionClosed:
                    print("WebSocket closed while sending frame.")
                    break
                except Exception as e:
                    print("Error sending frame:", e)
                    await asyncio.sleep(0.02)
                    continue

                # send hazard JSONs if detected
                try:
                    # pothole hazard (send once per frame, avoid duplicate logging by frame)
                    if pothole_detected and frame_count not in DETECTED_FRAMES_HISTORY:
                        DETECTED_FRAMES_HISTORY.add(frame_count)
                        hazard = {"type": HAZARD_TYPE_POTHOLE, "severity": 8, "frame_id": frame_count, "count": len(pothole_boxes)}
                        await websocket.send(json.dumps(hazard))
                        log_hazard(hazard["type"], severity=hazard["severity"], frame_id=frame_count)
                        print("⚠️ Pothole hazard sent & logged.", hazard)

                    # rash hazards
                    for alert in rash_alerts:
                        hazard = {"type": HAZARD_TYPE_RASH, "severity": max(1, int(alert['speed'] // 5)), "frame_id": frame_count, "track_id": int(alert['track_id'])}
                        await websocket.send(json.dumps(hazard))
                        log_hazard(hazard["type"], severity=hazard["severity"], frame_id=frame_count)
                        print("⚠️ Rash hazard sent & logged:", hazard)

                except ConnectionClosed:
                    print("WebSocket closed while sending hazard JSON.")
                    break
                except Exception as e:
                    print("Error sending hazard JSON:", e)

                await asyncio.sleep(0.03)  # small throttle

            print("✅ Video stream loop ended.")

    except Exception as e:
        print("❌ Unexpected Error (WS connect/loop):", e)

    finally:
        cap.release()
        print("✅ Video capture released.")
        print("✅ Script exited cleanly.")


if __name__ == "__main__":
    asyncio.run(stream_video_and_detect())
