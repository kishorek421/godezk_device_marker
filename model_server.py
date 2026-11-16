"""Local YOLO prediction service for Godezk Marker auto-labeling.

Serves POST /predict for the studio 'http' predictor. Model-agnostic:
any Ultralytics-compatible .pt weights file works — swap MODEL_WEIGHTS
to change domains (helmet detection today, anything else tomorrow)
without touching the labeling pipeline.

Env:
  MODEL_WEIGHTS  path to .pt file (default: helmet.pt, fallback yolov8n.pt)
  PORT           listen port (default: 9000)

Run:  python model_server.py
"""
import base64
import io
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WEIGHTS = os.environ.get('MODEL_WEIGHTS', 'helmet.pt')
PORT = int(os.environ.get('PORT', '9000'))

from ultralytics import YOLO  # noqa: E402

model = YOLO(WEIGHTS if os.path.exists(WEIGHTS) else 'yolov8n.pt')
print(f'Model: {model.ckpt_path if hasattr(model, "ckpt_path") else WEIGHTS}')
print(f'Classes: {model.names}')


class Predict(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/predict':
            self.send_error(404)
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            from PIL import Image
            image = Image.open(io.BytesIO(base64.b64decode(body['image_b64'])))
            results = model.predict(image, verbose=False)[0]
            names = model.names
            w, h = image.size
            detections = []
            for b in results.boxes:
                x1, y1, x2, y2 = (v / s for v, s in zip(b.xyxy[0].tolist(), [w, h, w, h]))
                detections.append({'label': names[int(b.cls)], 'type': 'box',
                                   'points': [[x1, y1], [x2, y2]], 'score': round(float(b.conf), 4)})
            self._reply({'detections': detections})
        except Exception as exc:
            self._reply({'detections': [], 'error': str(exc)}, status=500)

    def do_GET(self):
        if self.path == '/health':
            self._reply({'status': 'ok', 'classes': list(model.names.values())})
        else:
            self.send_error(404)

    def _reply(self, data, status=200):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    print(f'Listening on http://127.0.0.1:{PORT}/predict')
    ThreadingHTTPServer(('127.0.0.1', PORT), Predict).serve_forever()
